from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Optional

from ..database import get_db
from ..models import User, OLT, OLTPort
from ..schemas import PONStatusResponse, ONUFullInfo
from ..auth import get_current_user
from ..olt_client import (
    get_olt_client, OLTConnectionError,
    parse_onu_state, parse_onu_detail, parse_onu_power,
    parse_onu_distance, parse_onu_wan, parse_onu_voip,
    parse_onu_temperature, parse_onu_firmware,
    parse_onu_baseinfo, parse_uncfg_onus, parse_olt_rx_power,
    _olt_iface, _onu_iface
)
from ..redis_client import cache

router = APIRouter(prefix="/onus", tags=["ONUs"])


def _get_olt_or_404(olt_id: int, db: Session) -> OLT:
    olt = db.query(OLT).filter(OLT.id == olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")
    return olt


def _get_port_pon(olt_id: int, slot: int, pon: int, db: Session) -> Optional[OLTPort]:
    """Busca a porta no banco pelo slot e pon."""
    return db.query(OLTPort).filter(
        OLTPort.olt_id == olt_id,
        OLTPort.slot == slot,
        OLTPort.pon == pon
    ).first()


@router.get("/{olt_id}/pon/{slot}/{pon}/status")
def get_pon_status(
    olt_id: int,
    slot: int,
    pon: int,
    force_refresh: bool = Query(False, description="Forçar atualização ignorando cache"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retorna o status de todas as ONUs de uma porta PON.
    Interface: gpon-olt_SLOT/PON
    Cache Redis por 24 horas. Use force_refresh=true para atualizar.
    """
    olt = _get_olt_or_404(olt_id, db)
    port_obj = _get_port_pon(olt_id, slot, pon, db)

    cache_key = cache.key_pon_status(olt_id, slot, pon)

    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            cache_info = cache.get_cache_info(cache_key)
            cached_data["cached"] = True
            cached_data["cache_expires_in"] = cache_info.get("expires_in")
            return cached_data

    iface = _olt_iface(slot, pon)

    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()

        # Status das ONUs
        output = client.execute_command(f"show gpon onu state {iface}")
        onus = parse_onu_state(output)

        # Potência RX da OLT
        rx_output = client.execute_command(f"show pon power olt-rx {iface}")
        olt_rx_list = parse_olt_rx_power(rx_output)

        client.disconnect()

        # Mescla potência RX com dados das ONUs
        rx_map = {r["onu_index"]: r for r in olt_rx_list}
        for onu in onus:
            rx_info = rx_map.get(onu["onu_index"], {})
            onu["olt_rx_power"] = rx_info.get("olt_rx_power")
            onu["olt_rx_status"] = rx_info.get("olt_rx_status", "unknown")

        # Atualiza contagem de ONUs na porta
        if port_obj:
            port_obj.onu_count = len(onus)
            port_obj.status = "online" if onus else "active"
            db.commit()

        result = {
            "olt_id": olt_id,
            "slot": slot,
            "pon": pon,
            "olt_interface": iface,
            "onus": onus,
            "total": len(onus),
            "online": sum(1 for o in onus if o["oper_state"] == "working"),
            "offline": sum(1 for o in onus if o["oper_state"] == "disable"),
            "cached": False,
            "cache_expires_in": None,
            "last_updated": datetime.utcnow().isoformat()
        }

        cache.set(cache_key, result)
        return result

    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/{olt_id}/pon/{slot}/{pon}/onu/{onu_id}/full")
def get_onu_full_info(
    olt_id: int,
    slot: int,
    pon: int,
    onu_id: int,
    force_refresh: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Retorna informações completas de uma ONU específica.
    Interface: gpon-onu_SLOT/PON:ONU_ID
    """
    olt = _get_olt_or_404(olt_id, db)
    cache_key = cache.key_onu_full(olt_id, slot, pon, onu_id)

    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            cache_info = cache.get_cache_info(cache_key)
            cached_data["cached"] = True
            cached_data["cache_expires_in"] = cache_info.get("expires_in")
            return cached_data

    onu_r = _onu_iface(slot, pon, onu_id)
    onu_idx = f"{slot}/{pon}:{onu_id}"

    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()

        result = {"onu_index": onu_idx, "olt_id": olt_id, "onu_interface": onu_r}

        # 1. Estado
        out = client.execute_command(f"show gpon onu state {onu_r}")
        states = parse_onu_state(out)
        if states:
            result["status"] = states[0]

        # 2. Detalhes
        out = client.execute_command(f"show gpon onu detail-info {onu_r}")
        result["detail"] = parse_onu_detail(out, onu_idx)

        # 3. Potência
        out = client.execute_command(f"show pon power attenuation {onu_r}")
        result["power"] = parse_onu_power(out, onu_idx)

        # 4. Distância
        out = client.execute_command(f"show gpon onu distance {onu_r}")
        result["distance"] = parse_onu_distance(out, onu_idx)

        # 5. WAN
        out = client.execute_command(f"show gpon remote-onu wan-info {onu_r}")
        result["wan"] = parse_onu_wan(out, onu_idx)

        # 6. VoIP
        out = client.execute_command(f"show gpon remote-onu voip-status {onu_r}")
        result["voip"] = parse_onu_voip(out, onu_idx)

        # 7. Temperatura
        out = client.execute_command(f"show gpon onu temperature {onu_r}")
        result["temperature"] = parse_onu_temperature(out, onu_idx)

        # 8. Firmware
        out = client.execute_command(f"show gpon onu firmware-version {onu_r}")
        result["firmware"] = parse_onu_firmware(out, onu_idx)

        client.disconnect()

        result["cached"] = False
        result["last_updated"] = datetime.utcnow().isoformat()

        cache.set(cache_key, result)
        return result

    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/{olt_id}/pon/{slot}/{pon}/baseinfo")
def get_pon_baseinfo(
    olt_id: int,
    slot: int,
    pon: int,
    force_refresh: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retorna informações base (SN, modelo, estado) de todas as ONUs provisionadas."""
    olt = _get_olt_or_404(olt_id, db)
    cache_key = f"olt:{olt_id}:pon:{slot}:{pon}:baseinfo"

    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            cached_data["cached"] = True
            return cached_data

    iface = _olt_iface(slot, pon)

    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()
        out = client.execute_command(f"show gpon onu baseinfo {iface}")
        client.disconnect()

        onus = parse_onu_baseinfo(out)
        result = {
            "olt_id": olt_id, "slot": slot, "pon": pon,
            "olt_interface": iface,
            "onus": onus, "total": len(onus),
            "cached": False, "last_updated": datetime.utcnow().isoformat()
        }
        cache.set(cache_key, result)
        return result
    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/{olt_id}/unconfigured")
def get_unconfigured_onus(
    olt_id: int,
    force_refresh: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Retorna ONUs não provisionadas (aguardando autorização)."""
    olt = _get_olt_or_404(olt_id, db)
    cache_key = cache.key_uncfg_onus(olt_id)

    if not force_refresh:
        cached_data = cache.get(cache_key)
        if cached_data:
            cached_data["cached"] = True
            return cached_data

    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()
        out = client.execute_command("show gpon onu uncfg")
        client.disconnect()

        onus = parse_uncfg_onus(out)
        result = {
            "olt_id": olt_id,
            "onus": onus,
            "total": len(onus),
            "cached": False,
            "last_updated": datetime.utcnow().isoformat()
        }
        cache.set(cache_key, result)
        return result
    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.delete("/{olt_id}/cache")
def clear_olt_cache(
    olt_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Limpa todo o cache Redis de uma OLT."""
    _get_olt_or_404(olt_id, db)
    deleted = cache.delete_pattern(f"olt:{olt_id}:*")
    return {"message": f"Cache limpo: {deleted} chave(s) removida(s)"}


@router.delete("/{olt_id}/pon/{slot}/{pon}/cache")
def clear_pon_cache(
    olt_id: int,
    slot: int,
    pon: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Limpa o cache Redis de uma porta PON específica."""
    _get_olt_or_404(olt_id, db)
    deleted = cache.delete_pattern(f"olt:{olt_id}:pon:{slot}:{pon}:*")
    deleted += cache.delete_pattern(f"olt:{olt_id}:onu:{slot}:{pon}:*")
    return {"message": f"Cache da PON limpo: {deleted} chave(s) removida(s)"}


@router.get("/{olt_id}/search")
def search_onu(
    olt_id: int,
    serial: Optional[str] = Query(None, description="Número de série da ONU"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Busca uma ONU pelo número de série em todas as portas PON."""
    olt = _get_olt_or_404(olt_id, db)
    ports = db.query(OLTPort).filter(OLTPort.olt_id == olt_id).all()

    if not ports:
        raise HTTPException(
            status_code=404,
            detail="Nenhuma porta PON descoberta. Execute a descoberta primeiro."
        )

    if not serial:
        raise HTTPException(status_code=400, detail="Informe o número de série (serial)")

    results = []
    try:
        client = get_olt_client(olt.ip, olt.port, olt.username, olt.password, olt.protocol)
        client.connect()

        for p in ports:
            iface = _olt_iface(p.slot, p.pon)
            out = client.execute_command(f"show gpon onu baseinfo {iface}")
            onus = parse_onu_baseinfo(out)
            for onu in onus:
                if serial.upper() in onu.get("serial", "").upper():
                    onu["slot"] = p.slot
                    onu["pon"] = p.pon
                    onu["olt_interface"] = iface
                    results.append(onu)

        client.disconnect()
    except OLTConnectionError as e:
        raise HTTPException(status_code=503, detail=str(e))

    return {"results": results, "total": len(results), "serial_searched": serial}
