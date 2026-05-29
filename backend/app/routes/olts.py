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
    get_olt_client, parse_software_version, parse_onu_state, _olt_iface
)
from ..redis_client import cache

router = APIRouter(prefix="/olts", tags=["OLTs"])


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
        snmp_community=body.snmp_community,
        snmp_version=body.snmp_version,
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
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    success, output = test_olt_connection(
        olt.ip, olt.port, olt.username, olt.password, olt.protocol
    )

    olt.status = "online" if success else "offline"
    olt.last_check = datetime.utcnow()

    if success:
        info = parse_software_version(output)
        if info.get("firmware"):
            olt.firmware = info["firmware"]
        if info.get("model"):
            olt.model = info["model"]

    db.commit()

    return {
        "success": success,
        "status": olt.status,
        "output": output[:2000] if output else "",
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
    Descobre as portas PON da OLT e salva no banco.
    Sintaxe ZTE Titan: gpon-olt_SLOT/PON
    Após descoberta, atualiza contagem de ONUs em background.
    """
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    try:
        ports = discover_olt_ports(
            olt.ip, olt.port, olt.username, olt.password, olt.protocol
        )
    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))

    # Remove portas antigas e insere as novas
    db.query(OLTPort).filter(OLTPort.olt_id == olt_id).delete()

    for p in ports:
        port_obj = OLTPort(
            olt_id=olt_id,
            slot=p["slot"],
            pon=p["pon"],
            port_type=p.get("port_type", "gpon"),
            description=p.get("description", _olt_iface(p["slot"], p["pon"])),
            status="unknown",
            onu_count=0
        )
        db.add(port_obj)

    olt.status = "online"
    olt.last_check = datetime.utcnow()
    db.commit()

    # Invalida todo o cache da OLT
    cache.delete_pattern(f"olt:{olt_id}:*")

    # Atualiza contagem de ONUs em background
    background_tasks.add_task(
        _update_ports_onu_count,
        olt_id, olt.ip, olt.port, olt.username, olt.password, olt.protocol
    )

    return {
        "message": f"Descoberta concluída: {len(ports)} porta(s) PON encontrada(s). Contagem de ONUs sendo atualizada...",
        "ports_found": len(ports),
        "ports": [
            {
                "slot": p["slot"],
                "pon": p["pon"],
                "interface": _olt_iface(p["slot"], p["pon"]),
                "type": p.get("port_type", "gpon")
            }
            for p in ports
        ]
    }


def _update_ports_onu_count(olt_id: int, ip: str, port: int, username: str, password: str, protocol: str):
    """
    Tarefa em background: conecta na OLT e atualiza status e contagem de ONUs
    de cada porta PON descoberta.
    """
    from ..database import SessionLocal
    from ..olt_client import get_olt_client, parse_onu_state, OLTConnectionError, _olt_iface

    db = SessionLocal()
    try:
        ports = db.query(OLTPort).filter(OLTPort.olt_id == olt_id).all()
        if not ports:
            return

        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        for p in ports:
            iface = _olt_iface(p.slot, p.pon)
            try:
                out = client.execute_command(f"show gpon onu state {iface}", timeout=12)
                onus = parse_onu_state(out)
                p.onu_count = len(onus)
                online = sum(1 for o in onus if o.get("oper_state") == "working")
                if len(onus) > 0:
                    p.status = "online"
                elif out.strip():
                    p.status = "active"  # porta existe mas sem ONUs
                else:
                    p.status = "unknown"
            except Exception as ex:
                print(f"[bg] Erro em {iface}: {ex}")
                p.status = "unknown"

        client.disconnect()
        db.commit()
        print(f"[bg] Contagem de ONUs atualizada para OLT {olt_id}")
    except Exception as e:
        print(f"[bg] Erro ao atualizar contagem de ONUs OLT {olt_id}: {e}")
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
        OLTPort.slot, OLTPort.pon
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
