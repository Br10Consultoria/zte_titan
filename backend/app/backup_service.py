import gzip
import hashlib
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import httpx
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import BackupJob, BackupSettings, OLT
from .olt_client import OLTConnectionError, get_olt_client

logger = logging.getLogger("routes.backups")

DATA_DIR = Path(os.getenv("APP_DATA_DIR", "/app/data"))
FTP_DIR = DATA_DIR / "backup_ftp"
BACKUP_DIR = DATA_DIR / "backups"

_ftp_lock = threading.Lock()
_ftp_server: Optional[FTPServer] = None
_ftp_signature = None


class LoggingFTPHandler(FTPHandler):
    def on_connect(self):
        logger.info(f"[FTP] Conexao de {self.remote_ip}:{self.remote_port}")

    def on_disconnect(self):
        logger.info(f"[FTP] Desconectado {self.remote_ip}:{self.remote_port}")

    def on_login(self, username):
        logger.info(f"[FTP] Login OK usuario={username} origem={self.remote_ip}")

    def on_login_failed(self, username, password):
        logger.warning(f"[FTP] Login falhou usuario={username} origem={self.remote_ip}")

    def on_file_received(self, file):
        try:
            size = Path(file).stat().st_size
        except Exception:
            size = 0
        logger.info(f"[FTP] Arquivo recebido: {file} ({size} bytes)")

    def on_incomplete_file_received(self, file):
        logger.warning(f"[FTP] Arquivo incompleto recebido: {file}")


def _ensure_dirs():
    FTP_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)


def _parse_passive_ports(value: str):
    if not value:
        return range(30000, 30010)
    if "-" in value:
        start, end = value.split("-", 1)
        return range(int(start), int(end) + 1)
    return [int(p.strip()) for p in value.split(",") if p.strip()]


def get_or_create_settings(db: Session) -> BackupSettings:
    settings = db.query(BackupSettings).filter(BackupSettings.id == 1).first()
    if settings:
        return settings
    settings = BackupSettings(id=1)
    db.add(settings)
    db.commit()
    db.refresh(settings)
    return settings


def ensure_ftp_server(settings: BackupSettings) -> bool:
    global _ftp_server, _ftp_signature
    _ensure_dirs()
    if not settings.ftp_user or not settings.ftp_password:
        logger.warning("[BACKUP] FTP nao iniciado: usuario/senha nao configurados")
        return False

    signature = (
        settings.ftp_bind_host,
        settings.ftp_port,
        settings.ftp_user,
        settings.ftp_password,
        settings.ftp_passive_ports,
    )

    with _ftp_lock:
        if _ftp_server and _ftp_signature == signature:
            return True

        if _ftp_server:
            try:
                _ftp_server.close_all()
            except Exception:
                pass
            _ftp_server = None

        authorizer = DummyAuthorizer()
        authorizer.add_user(settings.ftp_user, settings.ftp_password, str(FTP_DIR), perm="elradfmwMT")

        handler = LoggingFTPHandler
        handler.authorizer = authorizer
        handler.passive_ports = _parse_passive_ports(settings.ftp_passive_ports)
        if settings.server_ip:
            handler.masquerade_address = settings.server_ip

        address = (settings.ftp_bind_host or "0.0.0.0", int(settings.ftp_port or 21))
        server = FTPServer(address, handler)
        thread = threading.Thread(target=server.serve_forever, name="zte-backup-ftp", daemon=True)
        thread.start()

        _ftp_server = server
        _ftp_signature = signature
        logger.info(f"[BACKUP] FTP iniciado em {address[0]}:{address[1]} dir={FTP_DIR}")
        return True


def ftp_status(db: Session) -> dict:
    settings = get_or_create_settings(db)
    files = []
    _ensure_dirs()
    for path in sorted(FTP_DIR.glob("*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.is_file():
            stat = path.stat()
            files.append({
                "name": path.name,
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            })
    return {
        "running": _ftp_server is not None,
        "bind": settings.ftp_bind_host,
        "port": settings.ftp_port,
        "server_ip": settings.server_ip,
        "passive_ports": settings.ftp_passive_ports,
        "ftp_dir": str(FTP_DIR),
        "files": files[:20],
    }


def sha256sum(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def send_telegram(settings: BackupSettings, path: Path, caption: str):
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError("Telegram nao configurado")
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendDocument"
    with path.open("rb") as f:
        files = {"document": (path.name, f, "application/gzip")}
        data = {"chat_id": settings.telegram_chat_id, "caption": caption}
        resp = httpx.post(url, data=data, files=files, timeout=120)
    resp.raise_for_status()


def test_telegram(db: Session) -> dict:
    settings = get_or_create_settings(db)
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        raise RuntimeError("Configure token e chat ID do Telegram")
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
    resp = httpx.post(
        url,
        data={
            "chat_id": settings.telegram_chat_id,
            "text": "Teste de notificacao do Br10Manager OLTS.",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return {"success": True}


def run_backup_job(job_id: int, send_telegram_flag: bool = True):
    db = SessionLocal()
    client = None
    job = db.query(BackupJob).filter(BackupJob.id == job_id).first()
    try:
        _ensure_dirs()
        if not job:
            return

        settings = get_or_create_settings(db)
        olt = db.query(OLT).filter(OLT.id == job.olt_id).first()
        if not olt:
            raise RuntimeError("OLT nao encontrada")
        if not settings.server_ip:
            raise RuntimeError("Configure o IP do servidor para o FTP")
        if not settings.ftp_password:
            raise RuntimeError("Configure a senha do usuario FTP")

        ensure_ftp_server(settings)

        now = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_olt = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in olt.name)[:40]
        ftp_filename = f"olt_{olt.id}_{safe_olt}_startrun.dat"
        ftp_path = FTP_DIR / ftp_filename
        final_dat = BACKUP_DIR / f"olt_{olt.id}_{safe_olt}_{olt.ip}_{now}_startrun.dat"
        final_gz = Path(str(final_dat) + ".gz")

        if ftp_path.exists():
            ftp_path.unlink()

        command = (
            f"copy ftp root: {settings.source_path} "
            f"//{settings.server_ip}/{ftp_filename}@{settings.ftp_user}:{settings.ftp_password}"
        )

        logger.info(f"[BACKUP] Executando backup da OLT {olt.id} {olt.ip}")
        logger.info(f"[BACKUP] Aguardando arquivo FTP em {ftp_path}")
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()
        output = client.execute_command(command, timeout=180)
        safe_output = output or ""
        if settings.ftp_password:
            safe_output = safe_output.replace(settings.ftp_password, "***")
        job.command_output = safe_output[-8000:] if safe_output else ""
        db.commit()
        logger.info(f"[BACKUP] Saida do comando copy ftp ({len(safe_output)} chars): {safe_output[-1000:]}")

        deadline = time.time() + 120
        while time.time() < deadline:
            if ftp_path.exists() and ftp_path.stat().st_size > 1024:
                break
            time.sleep(2)
        else:
            existing = [
                f"{p.name} ({p.stat().st_size} bytes)"
                for p in FTP_DIR.glob("*")
                if p.is_file()
            ]
            raise RuntimeError(
                "Backup nao chegou no FTP ou arquivo veio vazio. "
                f"Esperado: {ftp_filename}. Arquivos no FTP: {existing or 'nenhum'}"
            )

        shutil.copy2(ftp_path, final_dat)
        file_hash = sha256sum(final_dat)

        with final_dat.open("rb") as src, gzip.open(final_gz, "wb") as dst:
            shutil.copyfileobj(src, dst)

        if not settings.keep_local and final_dat.exists():
            final_dat.unlink()

        caption = (
            f"Backup ZTE\n"
            f"OLT: {olt.name} ({olt.ip})\n"
            f"Data: {now}\n"
            f"SHA256: {file_hash}"
        )

        telegram_sent = False
        if settings.telegram_enabled and send_telegram_flag:
            send_telegram(settings, final_gz, caption)
            telegram_sent = True

        job.status = "success"
        job.finished_at = datetime.utcnow()
        job.filename = final_gz.name
        job.file_path = str(final_gz)
        job.file_size = final_gz.stat().st_size
        job.sha256 = file_hash
        job.telegram_sent = telegram_sent
        job.message = "Backup concluido"
        db.commit()
        logger.info(f"[BACKUP] Backup concluido: {final_gz}")

    except Exception as exc:
        logger.error(f"[BACKUP] Falha no job {job_id}: {exc}", exc_info=True)
        if job:
            job.status = "failed"
            job.finished_at = datetime.utcnow()
            job.message = str(exc)
            db.commit()
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass
        db.close()
