"""
SNMP Client para ZTE Titan (C320/C600/C610/C620/C650)
Usa subprocess com snmpwalk/snmpget do sistema operacional.

OIDs proprietárias ZTE C320:
  3902.1012.3.13.1.1.1   - Nome das interfaces PON (discovery, retorna "OLT-1", "OLT-2"...)
  3902.1012.3.13.1.1.13  - Contagem de ONUs por porta PON (indexed por if_index ZTE)
  3902.1015.3.1.13.1.12  - Temperatura SFP por porta PON
  3902.1015.3.1.13.1.4   - Potência laser TX por porta PON

Codificação do ifIndex ZTE C320:
  BASE = 268435456 (0x10000000)
  ifIndex = BASE + (slot-1)*65536 + (pon-1)*256
  Decodificação:
    diff  = ifIndex - BASE
    slot  = (diff >> 16) + 1
    pon   = ((diff >> 8) & 0xFF) + 1

Instalação no servidor:
  apt-get install -y snmp
"""
import re
import subprocess
import shutil
from typing import List, Dict, Optional, Tuple

# OIDs ZTE proprietárias (prefixo 1.3.6.1.4.1.3902)
OID_ZTE_PON_NAME       = "1.3.6.1.4.1.3902.1012.3.13.1.1.1"   # Nome da porta (OLT-1, OLT-2...)
OID_ZTE_PON_ONU_COUNT  = "1.3.6.1.4.1.3902.1012.3.13.1.1.13"  # Qtd ONUs por porta
OID_ZTE_PON_TEMP       = "1.3.6.1.4.1.3902.1015.3.1.13.1.12"  # Temperatura SFP
OID_ZTE_PON_TX_POWER   = "1.3.6.1.4.1.3902.1015.3.1.13.1.4"   # Potência laser TX

# OIDs padrão MIB-II
OID_SYS_DESCR          = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME           = "1.3.6.1.2.1.1.5.0"
OID_IF_OPER_STATUS     = "1.3.6.1.2.1.2.2.1.8"

# Constante de codificação ZTE
ZTE_IF_INDEX_BASE = 268435456  # 0x10000000


class SNMPError(Exception):
    pass


def _find_snmp_tool(name: str) -> Optional[str]:
    """Localiza o binário snmpwalk/snmpget no sistema."""
    path = shutil.which(name)
    if path:
        return path
    for candidate in [f"/usr/bin/{name}", f"/usr/local/bin/{name}"]:
        import os
        if os.path.isfile(candidate):
            return candidate
    return None


def _zte_if_index_to_slot_pon(if_index: int) -> Tuple[int, int]:
    """
    Converte ifIndex ZTE para (slot, pon).
    BASE = 268435456
    slot = ((if_index - BASE) >> 16) + 1
    pon  = (((if_index - BASE) >> 8) & 0xFF) + 1
    """
    diff = if_index - ZTE_IF_INDEX_BASE
    slot = (diff >> 16) + 1
    pon  = ((diff >> 8) & 0xFF) + 1
    return slot, pon


def _zte_slot_pon_to_if_index(slot: int, pon: int) -> int:
    """Converte (slot, pon) para ifIndex ZTE."""
    return ZTE_IF_INDEX_BASE + (slot - 1) * 65536 + (pon - 1) * 256


def _snmp_walk(host: str, community: str, oid: str, port: int = 161,
               version: str = "2c", timeout: int = 15) -> List[Tuple[str, str]]:
    """
    Executa snmpwalk via subprocess.
    Retorna lista de (oid_suffix, value_str).
    O oid_suffix é a parte final do OID (ex: "268435456" para o índice).
    """
    tool = _find_snmp_tool("snmpwalk")
    if not tool:
        raise SNMPError("snmpwalk não encontrado. Instale com: apt-get install -y snmp")

    cmd = [
        tool,
        "-v", version,
        "-c", community,
        "-t", str(timeout),
        "-r", "2",
        "-On",   # OIDs em formato numérico
        f"{host}:{port}",
        oid
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 10
        )
    except subprocess.TimeoutExpired:
        raise SNMPError(f"Timeout ao executar snmpwalk em {host}:{port}")
    except FileNotFoundError:
        raise SNMPError("snmpwalk não encontrado. Instale com: apt-get install -y snmp")

    if result.returncode != 0 and not result.stdout.strip():
        raise SNMPError(f"snmpwalk falhou em {host}:{port}: {result.stderr.strip()[:200]}")

    rows = []
    oid_prefix = oid.strip('.')
    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line or '=' not in line:
            continue

        # Formato: .1.3.6.1.4.1.3902.1012.3.13.1.1.1.268435456 = STRING: "OLT-1"
        parts = line.split('=', 1)
        if len(parts) != 2:
            continue

        full_oid = parts[0].strip().strip('.')
        raw_val  = parts[1].strip()

        # Remove tipo (STRING:, INTEGER:, Hex-STRING:, etc.)
        val = re.sub(r'^\w[\w-]*:\s*', '', raw_val).strip().strip('"').strip()

        # Extrai o sufixo do OID (parte após o prefixo base)
        if full_oid.startswith(oid_prefix):
            suffix = full_oid[len(oid_prefix):].strip('.')
        else:
            suffix = full_oid.split('.')[-1]

        rows.append((suffix, val))

    return rows


def _snmp_get(host: str, community: str, oid: str, port: int = 161,
              version: str = "2c", timeout: int = 5) -> Optional[str]:
    """Executa snmpget via subprocess. Retorna o valor como string ou None."""
    tool = _find_snmp_tool("snmpget")
    if not tool:
        raise SNMPError("snmpget não encontrado. Instale com: apt-get install -y snmp")

    cmd = [
        tool,
        "-v", version,
        "-c", community,
        "-t", str(timeout),
        "-r", "1",
        "-On",
        f"{host}:{port}",
        oid
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout + 3
        )
    except subprocess.TimeoutExpired:
        raise SNMPError(f"Timeout ao executar snmpget em {host}:{port}")
    except FileNotFoundError:
        raise SNMPError("snmpget não encontrado. Instale com: apt-get install -y snmp")

    if result.returncode != 0:
        raise SNMPError(f"snmpget falhou: {result.stderr.strip()[:200]}")

    line = result.stdout.strip()
    if '=' not in line:
        return line if line else None

    val = line.split('=', 1)[1].strip()
    val = re.sub(r'^\w[\w-]*:\s*', '', val).strip().strip('"').strip()
    return val if val else None


def snmp_test_connection(host: str, community: str = "public", port: int = 161,
                         version: str = "2c") -> Tuple[bool, str]:
    """Testa conectividade SNMP com a OLT."""
    try:
        val = _snmp_get(host, community, OID_SYS_DESCR, port, version, timeout=5)
        if val:
            return True, val
        return False, "Sem resposta SNMP"
    except SNMPError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Erro SNMP: {str(e)}"


def snmp_get_system_info(host: str, community: str = "public", port: int = 161,
                         version: str = "2c") -> Dict:
    """Obtém informações básicas do sistema via SNMP."""
    result = {}
    try:
        sys_descr = _snmp_get(host, community, OID_SYS_DESCR, port, version)
        if sys_descr:
            result["sys_descr"] = sys_descr
            m = re.search(r'(C\d{3,4})', sys_descr, re.IGNORECASE)
            result["model"] = f"ZTE {m.group(1).upper()}" if m else "ZTE Titan"
            fw = re.search(r'[Vv](\d+\.\d+[\.\d]*)', sys_descr)
            if fw:
                result["firmware"] = fw.group(1)

        sys_name = _snmp_get(host, community, OID_SYS_NAME, port, version)
        if sys_name:
            result["sys_name"] = sys_name

    except Exception as e:
        result["error"] = str(e)

    return result


def snmp_discover_pon_ports(host: str, community: str = "public", port: int = 161,
                            version: str = "2c") -> List[Dict]:
    """
    Descobre todas as portas PON via SNMP usando a OID proprietária ZTE.
    OID: 1.3.6.1.4.1.3902.1012.3.13.1.1.1
    Retorna lista de dicts com slot, pon, if_index, status, onu_count.
    """
    if not _find_snmp_tool("snmpwalk"):
        raise SNMPError(
            "snmpwalk não encontrado. Instale com: apt-get install -y snmp"
        )

    # Walk na OID proprietária ZTE para listar portas PON
    try:
        pon_rows = _snmp_walk(host, community, OID_ZTE_PON_NAME, port, version)
    except SNMPError:
        raise
    except Exception as e:
        raise SNMPError(f"Falha no SNMP walk ZTE PON em {host}:{port} — {e}")

    if not pon_rows:
        raise SNMPError(
            f"Nenhuma porta PON encontrada via SNMP em {host}:{port}. "
            f"Verifique a community string '{community}' e se o SNMP está habilitado na OLT."
        )

    pon_ports = []
    for suffix, val in pon_rows:
        # suffix é o ifIndex ZTE (ex: "268435456")
        try:
            if_index = int(suffix)
        except ValueError:
            # Pode ser OID completo, pega o último número
            try:
                if_index = int(suffix.split('.')[-1])
            except ValueError:
                continue

        # Decodifica slot e pon a partir do ifIndex
        try:
            slot, pon = _zte_if_index_to_slot_pon(if_index)
        except Exception:
            continue

        # Valida: slot e pon devem ser razoáveis (1-8 slots, 1-16 pons)
        if not (1 <= slot <= 8 and 1 <= pon <= 16):
            continue

        pon_ports.append({
            "slot": slot,
            "pon": pon,
            "if_index": if_index,
            "if_name": f"gpon-olt_{slot}/{pon}",
            "port_type": "gpon",
            "description": val if val else f"OLT-{pon}",
            "status": "unknown",
            "onu_count": 0,
        })

    if not pon_ports:
        raise SNMPError(
            f"Nenhuma porta PON válida decodificada de {host}. "
            f"Total de entradas retornadas: {len(pon_rows)}. "
            f"Exemplos de índices: {[r[0] for r in pon_rows[:5]]}"
        )

    # Busca contagem de ONUs por porta
    try:
        onu_rows = _snmp_walk(host, community, OID_ZTE_PON_ONU_COUNT, port, version)
        onu_map = {}
        for suffix, val in onu_rows:
            try:
                idx = int(suffix.split('.')[-1]) if '.' in suffix else int(suffix)
                onu_map[idx] = int(val) if val.isdigit() else 0
            except (ValueError, AttributeError):
                pass
        for p in pon_ports:
            p["onu_count"] = onu_map.get(p["if_index"], 0)
    except Exception:
        pass  # onu_count fica 0

    # Busca status operacional via ifOperStatus (MIB-II padrão)
    try:
        oper_rows = _snmp_walk(host, community, OID_IF_OPER_STATUS, port, version)
        oper_map = {}
        for suffix, val in oper_rows:
            try:
                idx = int(suffix.split('.')[-1]) if '.' in suffix else int(suffix)
                oper_map[idx] = int(val) if val.isdigit() else 0
            except (ValueError, AttributeError):
                pass
        for p in pon_ports:
            oper = oper_map.get(p["if_index"], 0)
            if oper == 1:
                p["status"] = "online"
            elif oper == 2:
                p["status"] = "offline"
    except Exception:
        pass  # status fica "unknown"

    # Ordena por slot, pon
    pon_ports.sort(key=lambda x: (x["slot"], x["pon"]))
    return pon_ports


def snmp_get_pon_onu_count(host: str, community: str, slot: int, pon: int,
                           port: int = 161, version: str = "2c") -> int:
    """Retorna a contagem de ONUs em uma porta PON específica."""
    if_index = _zte_slot_pon_to_if_index(slot, pon)
    oid = f"{OID_ZTE_PON_ONU_COUNT}.{if_index}"
    try:
        val = _snmp_get(host, community, oid, port, version)
        return int(val) if val and val.isdigit() else 0
    except Exception:
        return 0


def snmp_get_pon_tx_power(host: str, community: str, slot: int, pon: int,
                          port: int = 161, version: str = "2c") -> Optional[float]:
    """Retorna a potência laser TX de uma porta PON em dBm."""
    if_index = _zte_slot_pon_to_if_index(slot, pon)
    oid = f"{OID_ZTE_PON_TX_POWER}.{if_index}"
    try:
        val = _snmp_get(host, community, oid, port, version)
        if val:
            # Valor em 0.001 dBm (multiplicador 0.001 conforme Zabbix)
            return round(float(val) * 0.001, 2)
    except Exception:
        pass
    return None
