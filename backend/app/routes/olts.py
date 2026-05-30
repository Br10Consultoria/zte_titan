"""
Rotas de gerenciamento de OLTs.
Usa formato ZTE Titan: gpon-olt_SLOT/CARD/PON (3 partes)
"""
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List

from ..database import get_db
from ..models import User, OLT, OLTPort
from ..schemas import OLTCreate, OLTUpdate, OLTResponse, OLTPortResponse
from ..auth import get_current_user, get_current_admin
from ..olt_client import (
    test_olt_connection, discover_olt_ports, OLTConnectionError,
    get_olt_client, parse_software_version, parse_onu_state
)
from ..snmp_client import (
    snmp_discover_pon_ports, snmp_get_system_info, snmp_test_connection, SNMPError
)
from ..redis_client import cache


def _olt_iface(slot: int, card: int, pon: int) -> str:
    """
    Gera referência de porta PON no formato ZTE C320: gpon-olt_RACK/SLOT/PON
    RACK=1 fixo, SLOT=número da placa (card), PON=porta
    Exemplo: card=1, pon=3 → gpon-olt_1/1/3 | card=2, pon=5 → gpon-olt_1/2/5
    """
    return f"gpon-olt_1/{card}/{pon}"

router = APIRouter(prefix="/olts", tags=["OLTs"])
logger = logging.getLogger("routes.olts")


@router.get("", response_model=List[OLTResponse])
def list_olts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    return db.query(OLT).all()


@router.post("", response_model=OLTResponse, status_code=201)
def create_olt(
    body: OLTCreate,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    olt = OLT(
        name=body.name,
        ip=body.ip,
        port=body.port,
        username=body.username,
        password=body.password,
        protocol=body.protocol,
        snmp_community=body.snmp_community or "public",
        snmp_version=body.snmp_version or "2c",
        status="unknown"
    )
    db.add(olt)
    db.commit()
    db.refresh(olt)
    return olt


@router.get("/{olt_id}", response_model=OLTResponse)
def get_olt(
    olt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")
    return olt


@router.put("/{olt_id}", response_model=OLTResponse)
def update_olt(
    olt_id: int,
    body: OLTUpdate,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(olt, field, value)

    db.commit()
    db.refresh(olt)
    cache.delete_pattern(f"olt:{olt_id}:*")
    return olt


@router.delete("/{olt_id}")
def delete_olt(
    olt_id: int,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    db.delete(olt)
    db.commit()
    cache.delete_pattern(f"olt:{olt_id}:*")
    return {"message": "OLT excluída com sucesso"}


@router.post("/{olt_id}/test-connection")
def test_connection(
    olt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Testa conectividade com a OLT via SNMP e SSH/Telnet."""
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    community = olt.snmp_community or "public"
    snmp_version = olt.snmp_version or "2c"
    details = {}

    # Testa SNMP
    snmp_ok, snmp_output = snmp_test_connection(olt.ip, community, 161, snmp_version)
    if snmp_ok:
        details["snmp"] = "ok"
        details["snmp_info"] = snmp_output[:500]
        info = snmp_get_system_info(olt.ip, community, 161, snmp_version)
        if info.get("model"):
            olt.model = info["model"]
        if info.get("firmware"):
            olt.firmware = info["firmware"]
    else:
        details["snmp"] = f"indisponível: {snmp_output}"

    # Testa SSH/Telnet
    ssh_ok, ssh_output = test_olt_connection(
        olt.ip, olt.port, olt.username, olt.password, olt.protocol
    )
    if ssh_ok:
        details["ssh_telnet"] = "ok"
        info = parse_software_version(ssh_output)
        if info.get("firmware") and not olt.firmware:
            olt.firmware = info["firmware"]
        if info.get("model") and not olt.model:
            olt.model = info["model"]
    else:
        details["ssh_telnet"] = f"falhou: {ssh_output[:200]}"

    success = snmp_ok or ssh_ok
    olt.status = "online" if success else "offline"
    olt.last_check = datetime.utcnow()
    db.commit()

    return {
        "success": success,
        "status": olt.status,
        "snmp_available": snmp_ok,
        "ssh_telnet_available": ssh_ok,
        "details": details,
        "message": "Conexão estabelecida com sucesso!" if success else "Falha na conexão"
    }


@router.post("/{olt_id}/discover")
def discover_ports(
    olt_id: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    """
    Descobre as portas PON da OLT.
    Tenta SNMP primeiro (rápido), depois SSH/Telnet como fallback.
    Formato ZTE Titan: gpon-olt_SLOT/CARD/PON (3 partes)
    """
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    community = olt.snmp_community or "public"
    snmp_version = olt.snmp_version or "2c"
    discovery_method = "snmp"
    ports = []
    error_msgs = []

    logger.info(f"[DISCOVER] Iniciando descoberta para OLT {olt.name} ({olt.ip})")

    # --- Tentativa 1: SNMP ---
    try:
        logger.info(f"[DISCOVER] Tentando SNMP com community '{community}'")
        # Passa credenciais SSH para detecção automática do card real
        snmp_ports = snmp_discover_pon_ports(
            olt.ip, community, 161, snmp_version,
            ssh_port=olt.port,
            ssh_username=olt.username,
            ssh_password=olt.password,
            ssh_protocol=olt.protocol
        )
        if snmp_ports:
            ports = snmp_ports
            logger.info(f"[DISCOVER] SNMP encontrou {len(ports)} portas")
            info = snmp_get_system_info(olt.ip, community, 161, snmp_version)
            if info.get("model"):
                olt.model = info["model"]
            if info.get("firmware"):
                olt.firmware = info["firmware"]
        else:
            logger.warning("[DISCOVER] SNMP retornou 0 portas, tentando SSH/Telnet")
            discovery_method = "ssh_telnet"
    except (SNMPError, Exception) as e:
        error_msgs.append(f"SNMP: {e}")
        discovery_method = "ssh_telnet"
        logger.warning(f"[DISCOVER] SNMP falhou: {e}")

    # --- Tentativa 2: SSH/Telnet ---
    if not ports:
        try:
            logger.info(f"[DISCOVER] Tentando SSH/Telnet ({olt.protocol}) em {olt.ip}:{olt.port}")
            ports = discover_olt_ports(
                olt.ip, olt.port, olt.username, olt.password, olt.protocol
            )
            logger.info(f"[DISCOVER] SSH/Telnet encontrou {len(ports)} portas")
        except OLTConnectionError as e:
            error_msgs.append(f"SSH/Telnet: {e}")
            raise HTTPException(
                status_code=503,
                detail=f"Descoberta falhou. Erros: {'; '.join(error_msgs)}"
            )

    # Remove portas antigas e insere as novas
    try:
        db.query(OLTPort).filter(OLTPort.olt_id == olt_id).delete()
        db.flush()
        logger.info(f"[DISCOVER] Portas antigas removidas, inserindo {len(ports)} novas")

        for p in ports:
            slot = p["slot"]
            card = p.get("card", 1)
            pon  = p["pon"]
            iface = _olt_iface(slot, card, pon)
            logger.debug(f"[DISCOVER] Inserindo porta: slot={slot} card={card} pon={pon} iface={iface}")
            port_obj = OLTPort(
                olt_id=olt_id,
                slot=slot,
                card=card,
                pon=pon,
                port_type=p.get("port_type", "gpon"),
                description=p.get("description", iface),
                status="unknown",
                onu_count=p.get("onu_count", 0)
            )
            db.add(port_obj)

        olt.status = "online"
        olt.last_check = datetime.utcnow()
        db.commit()
        logger.info(f"[DISCOVER] {len(ports)} portas salvas no banco com sucesso")
    except Exception as db_err:
        db.rollback()
        logger.error(f"[DISCOVER] Erro ao salvar portas no banco: {db_err}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar portas: {str(db_err)}")

    # Invalida todo o cache da OLT
    cache.delete_pattern(f"olt:{olt_id}:*")

    # Atualiza contagem de ONUs em background
    background_tasks.add_task(
        _update_ports_onu_count,
        olt_id, olt.ip, olt.port, olt.username, olt.password, olt.protocol
    )

    return {
        "message": f"Descoberta via {discovery_method.upper()}: {len(ports)} porta(s) PON encontrada(s). Contagem de ONUs sendo atualizada...",
        "ports_found": len(ports),
        "discovery_method": discovery_method,
        "ports": [
            {
                "slot":      p["slot"],
                "card":      p.get("card", 1),
                "pon":       p["pon"],
                "interface": _olt_iface(p["slot"], p.get("card", 1), p["pon"]),
                "type":      p.get("port_type", "gpon"),
                "onu_count": p.get("onu_count", 0),
            }
            for p in ports
        ]
    }


def _update_ports_onu_count(olt_id: int, ip: str, port: int,
                             username: str, password: str, protocol: str):
    """
    Tarefa em background: conecta na OLT e atualiza status e contagem de ONUs
    de cada porta PON descoberta.
    """
    from ..database import SessionLocal
    from ..olt_client import get_olt_client, parse_onu_state, OLTConnectionError, _olt_iface

    logger.info(f"[BG] Iniciando atualização de contagem de ONUs para OLT {olt_id}")
    db = SessionLocal()
    try:
        ports = db.query(OLTPort).filter(OLTPort.olt_id == olt_id).all()
        if not ports:
            logger.warning(f"[BG] Nenhuma porta encontrada para OLT {olt_id}")
            return

        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        for p in ports:
            iface = _olt_iface(p.slot, p.card, p.pon)
            try:
                logger.info(f"[BG] Consultando {iface}")
                out = client.execute_command(f"show gpon onu state {iface}", timeout=15)
                onus = parse_onu_state(out)
                p.onu_count = len(onus)
                if len(onus) > 0:
                    p.status = "online"
                elif out.strip() and "invalid" not in out.lower():
                    p.status = "active"
                else:
                    p.status = "unknown"
                logger.info(f"[BG] {iface}: {len(onus)} ONUs, status={p.status}")
            except Exception as ex:
                logger.error(f"[BG] Erro em {iface}: {ex}")
                p.status = "unknown"

        client.disconnect()
        db.commit()
        logger.info(f"[BG] Contagem de ONUs atualizada para OLT {olt_id}")

    except Exception as e:
        logger.error(f"[BG] Erro ao atualizar OLT {olt_id}: {e}", exc_info=True)
    finally:
        db.close()


@router.get("/{olt_id}/ports", response_model=List[OLTPortResponse])
def get_olt_ports(
    olt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    ports = db.query(OLTPort).filter(OLTPort.olt_id == olt_id).order_by(
        OLTPort.slot, OLTPort.card, OLTPort.pon
    ).all()
    return ports


@router.get("/{olt_id}/status")
def get_olt_full_status(
    olt_id: int,
    force_refresh: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retorna status geral da OLT com informações de hardware."""
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    cache_key = cache.key_olt_status(olt_id)

    if not force_refresh:
        cached = cache.get(cache_key)
        if cached:
            cached["cached"] = True
            return cached

    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()

        result = {"olt_id": olt_id, "name": olt.name, "ip": olt.ip}

        out = client.execute_command("show software")
        result["software"] = out[:1000]

        out = client.execute_command("show uptime")
        result["uptime"] = out[:500]

        client.disconnect()

        cache.set(cache_key, result)
        result["cached"] = False
        return result

    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))
