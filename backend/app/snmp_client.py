"""
SNMP Client para ZTE Titan (C320/C600/C610/C620/C650)
Usa subprocess com snmpwalk/snmpget do sistema operacional.

OIDs proprietárias ZTE:
  3902.1012.3.13.1.1.1   - Lista de portas PON (discovery)
  3902.1012.3.13.1.1.13  - Contagem de ONUs por porta
  3902.1015.3.1.13.1.12  - Temperatura SFP por porta
  3902.1015.3.1.13.1.4   - Potência laser TX por porta

Codificação do ifIndex ZTE C320:
  Os índices são blocos de 65536 por slot e 256 por pon.
  A base varia por modelo/firmware — detectamos automaticamente
  a partir do menor índice retornado pelo snmpwalk.

  slot = (idx - base) // 65536 + 1
  pon  = ((idx - base) % 65536) // 256 + 1

Instalação no servidor:
  apt-get install -y snmp
"""
import re
import subprocess
import shutil
from typing import List, Dict, Optional, Tuple

# OIDs ZTE proprietárias
OID_ZTE_PON_NAME      = "1.3.6.1.4.1.3902.1012.3.13.1.1.1"   # Nome da porta
OID_ZTE_PON_ONU_COUNT = "1.3.6.1.4.1.3902.1012.3.13.1.1.13"  # Qtd ONUs por porta
OID_ZTE_PON_TEMP      = "1.3.6.1.4.1.3902.1015.3.1.13.1.12"  # Temperatura SFP
OID_ZTE_PON_TX_POWER  = "1.3.6.1.4.1.3902.1015.3.1.13.1.4"   # Potência laser TX

# OIDs padrão MIB-II
OID_SYS_DESCR         = "1.3.6.1.2.1.1.1.0"
OID_SYS_NAME          = "1.3.6.1.2.1.1.5.0"
OID_IF_OPER_STATUS    = "1.3.6.1.2.1.2.2.1.8"

# Passo entre PONs e entre slots (constante em todos os modelos ZTE)
ZTE_PON_STEP  = 256    # 0x100
ZTE_SLOT_STEP = 65536  # 0x10000


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


def _decode_zte_index(if_index: int, base: int) -> Tuple[int, int]:
    """
    Converte ifIndex ZTE para (slot, pon) usando a base detectada.
    slot = (if_index - base) // 65536 + 1
    pon  = ((if_index - base) % 65536) // 256 + 1
    """
    diff = if_index - base
    slot = diff // ZTE_SLOT_STEP + 1
    pon  = (diff % ZTE_SLOT_STEP) // ZTE_PON_STEP + 1
    return slot, pon


def _encode_zte_index(slot: int, pon: int, base: int) -> int:
    """Converte (slot, pon) para ifIndex ZTE."""
    return base + (slot - 1) * ZTE_SLOT_STEP + (pon - 1) * ZTE_PON_STEP


def _snmp_walk(host: str, community: str, oid: str, port: int = 161,
               version: str = "2c", timeout: int = 15) -> List[Tuple[int, str]]:
    """
    Executa snmpwalk via subprocess.
    Retorna lista de (if_index_int, value_str).
    Extrai sempre o último número do OID como índice.
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
    for line in result.stdout.strip().split('\n'):
        line = line.strip()
        if not line or '=' not in line:
            continue

        parts = line.split('=', 1)
        if len(parts) != 2:
            continue

        full_oid = parts[0].strip()
        raw_val  = parts[1].strip()

        # Remove tipo (STRING:, INTEGER:, Hex-STRING:, etc.)
        val = re.sub(r'^\w[\w-]*:\s*', '', raw_val).strip().strip('"').strip()

        # Extrai o último número do OID (sempre é o índice)
        oid_numbers = re.findall(r'\d+', full_oid)
        if not oid_numbers:
            continue

        try:
            idx = int(oid_numbers[-1])
        except ValueError:
            continue

        rows.append((idx, val))

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
    Detecta automaticamente a base do ifIndex a partir dos dados retornados.
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

    # Filtra apenas índices grandes (> 1000) que são ifIndex ZTE reais
    valid_rows = [(idx, val) for idx, val in pon_rows if idx > 1000]

    if not valid_rows:
        raise SNMPError(
            f"Índices retornados não parecem ser ifIndex ZTE válidos: "
            f"{[r[0] for r in pon_rows[:5]]}"
        )

    # Detecta a base automaticamente: menor índice alinhado a 256
    # A base é o menor índice que, quando subtraído de si mesmo, dá slot=1, pon=1
    all_indices = sorted([idx for idx, _ in valid_rows])

    # A base é o menor índice menos (slot-1)*65536 - (pon-1)*256
    # Como não sabemos slot/pon, usamos: base = min_idx - (min_idx % ZTE_SLOT_STEP)
    # Mas isso pode não funcionar. Melhor: base = min_idx (assume que o menor é slot=1, pon=1)
    # Verificamos se os índices seguintes são consistentes (diferença de 256)
    min_idx = all_indices[0]

    # Verifica se a diferença entre índices consecutivos é múltipla de 256
    diffs = [all_indices[i+1] - all_indices[i] for i in range(min(len(all_indices)-1, 5))]
    step_ok = all(d % ZTE_PON_STEP == 0 for d in diffs)

    if step_ok:
        # Base é o menor índice (corresponde a slot=1, pon=1)
        base = min_idx
    else:
        # Fallback: tenta alinhar à fronteira de slot
        base = min_idx - (min_idx % ZTE_SLOT_STEP)

    # Decodifica todos os índices usando a base detectada
    pon_ports = []
    for if_index, val in valid_rows:
        try:
            slot, pon = _decode_zte_index(if_index, base)
        except Exception:
            continue

        # Valida: slot e pon devem ser razoáveis
        if not (1 <= slot <= 16 and 1 <= pon <= 16):
            continue

        pon_ports.append({
            "slot": slot,
            "pon": pon,
            "if_index": if_index,
            "if_name": f"gpon-olt_{slot}/{pon}",
            "port_type": "gpon",
            "description": val if val else f"gpon-olt_{slot}/{pon}",
            "status": "unknown",
            "onu_count": 0,
            "_base": base,  # Salva a base para uso posterior
        })

    if not pon_ports:
        raise SNMPError(
            f"Nenhuma porta PON válida decodificada de {host}. "
            f"Base detectada: {base}. "
            f"Índices: {all_indices[:5]}"
        )

    # Busca contagem de ONUs por porta
    try:
        onu_rows = _snmp_walk(host, community, OID_ZTE_PON_ONU_COUNT, port, version)
        onu_map = {idx: val for idx, val in onu_rows if idx > 1000}
        for p in pon_ports:
            val = onu_map.get(p["if_index"], "0")
            try:
                p["onu_count"] = int(val) if str(val).isdigit() else 0
            except (ValueError, TypeError):
                p["onu_count"] = 0
    except Exception:
        pass

    # Busca status operacional via ifOperStatus
    try:
        oper_rows = _snmp_walk(host, community, OID_IF_OPER_STATUS, port, version)
        oper_map = {idx: val for idx, val in oper_rows}
        for p in pon_ports:
            oper = oper_map.get(p["if_index"], "0")
            try:
                oper_int = int(oper)
                if oper_int == 1:
                    p["status"] = "online"
                elif oper_int == 2:
                    p["status"] = "offline"
                else:
                    p["status"] = "active"
            except (ValueError, TypeError):
                p["status"] = "active"
    except Exception:
        # Se ifOperStatus falhar, marca como active (porta existe)
        for p in pon_ports:
            if p["status"] == "unknown":
                p["status"] = "active"

    # Remove campo interno _base antes de retornar
    for p in pon_ports:
        p.pop("_base", None)

    # Ordena por slot, pon
    pon_ports.sort(key=lambda x: (x["slot"], x["pon"]))
    return pon_ports


def snmp_get_pon_tx_power(host: str, community: str, slot: int, pon: int,
                          base: int, port: int = 161,
                          version: str = "2c") -> Optional[float]:
    """Retorna a potência laser TX de uma porta PON em dBm."""
    if_index = _encode_zte_index(slot, pon, base)
    oid = f"{OID_ZTE_PON_TX_POWER}.{if_index}"
    try:
        val = _snmp_get(host, community, oid, port, version)
        if val:
            return round(float(val) * 0.001, 2)
    except Exception:
        pass
    return None
