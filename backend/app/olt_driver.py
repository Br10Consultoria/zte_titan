"""
OLT Driver — Abstração multi-modelo para ZTE Titan.

Cada modelo de OLT tem comandos e formatos de interface diferentes.
Este módulo fornece uma camada de abstração que encapsula essas diferenças.

Modelos suportados:
  zte_c600  — ZTE C600 (formato: gpon_olt-SLOT/CARD/PON)
  zte_c300  — ZTE C300 (formato: gpon-olt_SLOT/CARD/PON)

Como adicionar um novo modelo:
  1. Crie uma subclasse de OLTDriver
  2. Implemente os métodos abstratos
  3. Registre no dicionário DRIVERS
"""
import re
import logging
from typing import List, Dict, Optional, Any

logger = logging.getLogger("olt_driver")

# ============================================================
# CONSTANTES DE MODELO
# ============================================================

OLT_MODELS = {
    "zte_c600": {
        "label": "ZTE C600",
        "vendor": "ZTE",
        "series": "C600",
        # Interface: gpon_olt-SLOT/CARD/PON  |  gpon_onu-SLOT/CARD/PON:ID
    },
    "zte_c300": {
        "label": "ZTE C300",
        "vendor": "ZTE",
        "series": "C300",
        # Interface: gpon-olt_SLOT/CARD/PON  |  gpon-onu_SLOT/CARD/PON:ID
    },
    "parks_3000_4000": {
        "label": "Parks 3000/4000",
        "vendor": "Parks",
        "series": "3000/4000",
        # Interface: gponSLOT/PON  |  ONU exibida internamente como SLOT/1/PON:ID
    },
}


# ============================================================
# CLASSE BASE
# ============================================================

class OLTDriver:
    """Interface base para drivers de OLT."""

    model_key: str = "base"

    # --- Geração de interfaces ---

    def olt_iface(self, slot: int, card: int, pon: int) -> str:
        raise NotImplementedError

    def onu_iface(self, idx_or_slot, card: int = None, pon: int = None, onu_id: int = None) -> str:
        """
        Aceita dois formatos:
          onu_iface("1/1/12:1")           -> converte string para interface
          onu_iface(slot, card, pon, id)  -> usa parâmetros separados
        """
        if isinstance(idx_or_slot, str):
            # Formato: "SLOT/CARD/PON:ID" ou "SLOT/PON:ID"
            parts = idx_or_slot.replace(':', '/').split('/')
            if len(parts) == 4:
                return self._onu_iface_parts(int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
            elif len(parts) == 3:
                return self._onu_iface_parts(1, int(parts[0]), int(parts[1]), int(parts[2]))
            return idx_or_slot
        return self._onu_iface_parts(int(idx_or_slot), int(card), int(pon), int(onu_id))

    def _onu_iface_parts(self, slot: int, card: int, pon: int, onu_id: int) -> str:
        raise NotImplementedError

    # --- Comandos ---

    def cmd_onu_state(self, olt_iface: str) -> str:
        raise NotImplementedError

    def cmd_onu_baseinfo(self, olt_iface: str) -> str:
        raise NotImplementedError

    def cmd_onu_detail(self, onu_iface: str) -> str:
        raise NotImplementedError

    def cmd_olt_rx(self, olt_iface: str) -> str:
        raise NotImplementedError

    def cmd_onu_power(self, onu_iface: str) -> str:
        raise NotImplementedError

    def cmd_onu_service(self, onu_iface: str) -> str:
        return f"show gpon remote-onu service {onu_iface}"

    def cmd_onu_equip(self, onu_iface: str) -> str:
        return f"show gpon remote-onu equip {onu_iface}"

    def cmd_optical_module(self, olt_iface: str) -> str:
        return f"show optical-module-info {olt_iface}"

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        """
        Retorna lista de comandos para reiniciar a ONU.
        O reboot requer entrada no modo de gerenciamento e confirmação.
        """
        raise NotImplementedError

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        """Retorna comando para consultar tráfego da ONU (Bps/pps)."""
        raise NotImplementedError

    def cmd_backup_to_ftp(
        self,
        server_ip: str,
        filename: str,
        ftp_user: str,
        ftp_password: str,
        source_path: str,
    ) -> str:
        return f"copy ftp root: {source_path} //{server_ip}/{filename}@{ftp_user}:{ftp_password}"

    def cmd_uncfg_onus(self) -> str:
        return "show pon onu uncfg"

    def parse_uncfg_onus(self, output: str) -> List[Dict]:
        return []

    def parse_onu_traffic(self, output: str) -> dict:
        """Parseia output de show interface gpon_onu-... ou gpon-onu_..."""
        raise NotImplementedError

    def cmd_discover_ports(self) -> List[str]:
        """Retorna lista de comandos para descoberta de portas."""
        raise NotImplementedError

    # --- Parsers ---

    def parse_onu_state(self, output: str) -> List[Dict]:
        raise NotImplementedError

    def parse_onu_baseinfo(self, output: str) -> List[Dict]:
        raise NotImplementedError

    def parse_olt_rx(self, output: str) -> Dict[str, float]:
        raise NotImplementedError

    def parse_onu_detail(self, output: str) -> Dict:
        raise NotImplementedError

    def parse_onu_power(self, output: str) -> Dict:
        raise NotImplementedError

    def parse_discover_ports(self, output: str) -> List[Dict]:
        raise NotImplementedError

    def parse_onu_state_for_discover(self, output: str, slot: int, card: int, pon: int) -> bool:
        """Verifica se o output de show onu state indica porta válida."""
        return (
            output.strip() != "" and
            "invalid" not in output.lower() and
            "error" not in output.lower() and
            "not exist" not in output.lower() and
            "no such" not in output.lower() and
            "%" not in output
        )


# ============================================================
# PARSER DE TRÁFEGO (COMUM A TODOS OS MODELOS)
# ============================================================

def _parse_onu_traffic_common(output: str) -> dict:
    """
    Parseia output de 'show interface gpon_onu-S/C/P:ID' ou 'gpon-onu_...'.

    Exemplo de output ZTE C300/C610:
      ONU statistic:
         Input rate :             800704 Bps             1205 pps
         Output rate:            1629378 Bps             1565 pps
         Input bandwidth utilization :0.6%
         Output bandwidth utilization: N/A
      Interface peak rate:
         Input peak rate :             875532 Bps             1205 pps
         Output peak rate:            1703780 Bps             1715 pps
      Total statistic:
       Input :
          Bytes:288115074            Packets:576376
       Output:
          Bytes:995006374            Packets:932691
    """
    result = {
        "rx_bps":        None,
        "rx_pps":        None,
        "tx_bps":        None,
        "tx_pps":        None,
        "rx_bw_util":    None,
        "tx_bw_util":    None,
        "rx_peak_bps":   None,
        "tx_peak_bps":   None,
        "rx_total_bytes": None,
        "tx_total_bytes": None,
        "rx_total_pkts":  None,
        "tx_total_pkts":  None,
    }

    def _int(s):
        try:
            return int(s.replace(',', '').strip())
        except Exception:
            return None

    def _float(s):
        try:
            return float(s.replace('%', '').strip())
        except Exception:
            return None

    for line in output.split('\n'):
        line = line.strip()
        # Input rate :   800704 Bps   1205 pps
        m = re.match(r'Input rate\s*:\s*(\d+)\s*Bps\s*(\d+)\s*pps', line, re.IGNORECASE)
        if m:
            result['rx_bps'] = _int(m.group(1))
            result['rx_pps'] = _int(m.group(2))
            continue
        # Output rate:  1629378 Bps   1565 pps
        m = re.match(r'Output rate\s*:\s*(\d+)\s*Bps\s*(\d+)\s*pps', line, re.IGNORECASE)
        if m:
            result['tx_bps'] = _int(m.group(1))
            result['tx_pps'] = _int(m.group(2))
            continue
        # Input bandwidth utilization :0.6%
        m = re.match(r'Input bandwidth utilization\s*:\s*([\d\.]+)%', line, re.IGNORECASE)
        if m:
            result['rx_bw_util'] = _float(m.group(1))
            continue
        # Output bandwidth utilization: N/A  ou  1.2%
        m = re.match(r'Output bandwidth utilization\s*:\s*([\d\.]+)%', line, re.IGNORECASE)
        if m:
            result['tx_bw_util'] = _float(m.group(1))
            continue
        # Input peak rate :  875532 Bps  1205 pps
        m = re.match(r'Input peak rate\s*:\s*(\d+)\s*Bps', line, re.IGNORECASE)
        if m:
            result['rx_peak_bps'] = _int(m.group(1))
            continue
        # Output peak rate: 1703780 Bps  1715 pps
        m = re.match(r'Output peak rate\s*:\s*(\d+)\s*Bps', line, re.IGNORECASE)
        if m:
            result['tx_peak_bps'] = _int(m.group(1))
            continue
        # Bytes:288115074   Packets:576376  (dentro de Input)
        m = re.match(r'Bytes:(\d+)\s+Packets:(\d+)', line, re.IGNORECASE)
        if m:
            # Detecta se é Input ou Output pelo contexto anterior
            if result['rx_total_bytes'] is None:
                result['rx_total_bytes'] = _int(m.group(1))
                result['rx_total_pkts']  = _int(m.group(2))
            else:
                result['tx_total_bytes'] = _int(m.group(1))
                result['tx_total_pkts']  = _int(m.group(2))
            continue

    logger.debug(f"[PARSER] parse_onu_traffic: {result}")
    return result


# ============================================================
# DRIVER ZTE C600
# ============================================================

class ZTEC600Driver(OLTDriver):
    """
    Driver para ZTE C600.
    Formato de interface: gpon_olt-SLOT/CARD/PON  |  gpon_onu-SLOT/CARD/PON:ID
    """
    model_key = "zte_c600"

    def olt_iface(self, slot: int, card: int, pon: int) -> str:
        return f"gpon_olt-{slot}/{card}/{pon}"

    def _onu_iface_parts(self, slot: int, card: int, pon: int, onu_id: int) -> str:
        return f"gpon_onu-{slot}/{card}/{pon}:{onu_id}"

    def cmd_onu_state(self, olt_iface: str) -> str:
        return f"show gpon onu state {olt_iface}"

    def cmd_onu_baseinfo(self, olt_iface: str) -> str:
        return f"show gpon onu baseinfo {olt_iface}"

    def cmd_onu_detail(self, onu_iface: str) -> str:
        return f"show gpon onu detail-info {onu_iface}"

    def cmd_olt_rx(self, olt_iface: str) -> str:
        return f"show pon power olt-rx {olt_iface}"

    def cmd_onu_power(self, onu_iface: str) -> str:
        return f"show pon power attenuation {onu_iface}"

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        """
        Sequência de comandos para reboot da ONU no C600.
        Formato: gpon_onu-SLOT/CARD/PON:ID
        """
        return [
            f"pon-onu-mng {onu_iface}",
            "reboot",
            "y",
        ]

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        return f"show interface {onu_iface}"

    def parse_onu_traffic(self, output: str) -> dict:
        return _parse_onu_traffic_common(output)

    def cmd_discover_ports(self) -> List[str]:
        return [
            "show gpon onu state",
        ]

    def parse_onu_state(self, output: str) -> List[Dict]:
        """
        Parseia: show gpon onu state gpon_olt-SLOT/CARD/PON
        Formato: 1/1/1:1   enable   enable   working   1(GPON)
        """
        onus = []
        seen = set()
        for line in output.split('\n'):
            line = line.strip()
            m = re.match(
                r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\w+)\s+(\w+)\s+(\w+)',
                line
            )
            if not m:
                continue
            idx = m.group(1)
            if idx in seen:
                continue
            seen.add(idx)
            oper = m.group(4).lower()
            color = (
                "green"  if oper == "working"   else
                "red"    if oper in ("dyinggasp", "los", "losi", "lof", "poweroff") else
                "yellow" if oper in ("reboot", "omci-down", "deactive") else
                "gray"
            )
            onus.append({
                "onu_index":       idx,
                "admin_state":     m.group(2).lower(),
                "omcc_state":      m.group(3).lower(),
                "oper_state":      m.group(4),
                "last_down_cause": None,
                "status_color":    color,
            })
        logger.debug(f"[PARSER] parse_onu_state (C600): {len(onus)} ONUs")
        return onus

    def parse_onu_baseinfo(self, output: str) -> List[Dict]:
        """
        Parseia: show gpon onu baseinfo gpon_olt-SLOT/CARD/PON
        Formato: gpon_onu-1/3/16:1    ZTE-F601V6.    sn      SN:MONU007F8491         ready
        """
        onus = []
        seen = set()
        for line in output.split('\n'):
            line = line.strip()
            # Formato com prefixo gpon_onu- ou gpon-onu_
            m = re.match(
                r'^gpon[_-]onu[_-](\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+\S+\s+(\S+)',
                line
            )
            if m:
                idx    = m.group(1)
                model  = m.group(2)
                serial = m.group(3).replace("SN:", "").strip()
                if idx not in seen:
                    seen.add(idx)
                    onus.append({"onu_index": idx, "model": model, "serial": serial})
                continue
            # Formato sem prefixo (fallback)
            m2 = re.match(
                r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+(\S+)\s+(\S+)',
                line
            )
            if m2:
                idx    = m2.group(1)
                serial = m2.group(2)
                model  = m2.group(3)
                if idx not in seen:
                    seen.add(idx)
                    onus.append({"onu_index": idx, "model": model, "serial": serial})
        logger.debug(f"[PARSER] parse_onu_baseinfo (C600): {len(onus)} ONUs")
        return onus

    def parse_olt_rx(self, output: str) -> Dict[str, float]:
        """
        Parseia: show pon power olt-rx gpon_olt-SLOT/CARD/PON
        Formato: gpon_onu-1/3/16:1    -28.860(dbm)
        """
        rx_map = {}
        for line in output.split('\n'):
            line = line.strip()
            m = re.match(r'gpon[_-]onu[_-](\d+/\d+(?:/\d+)?:\d+)\s+([-\d\.]+)', line)
            if m:
                idx = m.group(1)
                try:
                    rx_map[idx] = float(m.group(2))
                except ValueError:
                    pass
        logger.debug(f"[PARSER] parse_olt_rx (C600): {len(rx_map)} ONUs")
        return rx_map

    def parse_onu_detail(self, output: str) -> Dict:
        """
        Parseia: show gpon onu detail-info gpon_onu-SLOT/CARD/PON:ID
        Extrai campos principais do detail-info.
        """
        result = {}
        fields = {
            "name":            r'Name\s*:\s*(.+)',
            "type":            r'Type\s*:\s*(\S+)',
            "state":           r'State\s*:\s*(\S+)',
            "admin_state":     r'Admin state\s*:\s*(\S+)',
            "phase_state":     r'Phase state\s*:\s*(\S+)',
            "config_state":    r'Config state\s*:\s*(\S+)',
            "serial":          r'Serial number\s*:\s*(\S+)',
            "description":     r'Description\s*:\s*(\S[^\n]*)',
            "distance":        r'ONU Distance\s*:\s*(\S+)',
            "online_duration": r'Online Duration\s*:\s*(\S[^\n]*)',
            "fec":             r'FEC\s*:\s*(\S+)',
        }
        for key, pattern in fields.items():
            m = re.search(pattern, output, re.IGNORECASE)
            if m:
                result[key] = m.group(1).strip()

        # Histórico de eventos (AuthpassTime / OfflineTime / Cause)
        history = []
        for m in re.finditer(
            r'(\d+)\s+([\d\-]+ [\d:]+)\s+([\d\-]+ [\d:]+)\s*(\S*)',
            output
        ):
            auth_t  = m.group(2).strip()
            off_t   = m.group(3).strip()
            cause   = m.group(4).strip()
            if auth_t and auth_t != "0000-00-00 00:00:00":
                history.append({
                    "authpass_time":  auth_t,
                    "offline_time":   off_t if off_t != "0000-00-00 00:00:00" else None,
                    "cause":          cause or None,
                })
        result["history"] = history
        return result

    def parse_onu_power(self, output: str) -> Dict:
        """
        Parseia: show pon power attenuation gpon_onu-SLOT/CARD/PON:ID
        Formato:
          up      Rx :-26.968(dbm)      Tx:2.463(dbm)        29.431(dB)
          down    Tx :6.623(dbm)        Rx:-22.678(dbm)      29.301(dB)
        """
        result = {}
        # up: OLT recebe (Rx) e ONU transmite (Tx)
        m_up = re.search(
            r'up\s+Rx\s*:\s*([-\d\.]+)\s*\(dbm\)\s+Tx\s*:\s*([-\d\.]+)\s*\(dbm\)\s+([\d\.]+)',
            output, re.IGNORECASE
        )
        if m_up:
            result["olt_rx_power"]    = float(m_up.group(1))
            result["onu_tx_power"]    = float(m_up.group(2))
            result["attenuation_up"]  = float(m_up.group(3))

        # down: OLT transmite (Tx) e ONU recebe (Rx)
        m_dn = re.search(
            r'down\s+Tx\s*:\s*([-\d\.]+)\s*\(dbm\)\s+Rx\s*:\s*([-\d\.]+)\s*\(dbm\)\s+([\d\.]+)',
            output, re.IGNORECASE
        )
        if m_dn:
            result["olt_tx_power"]      = float(m_dn.group(1))
            result["rx_power"]          = float(m_dn.group(2))
            result["attenuation_down"]  = float(m_dn.group(3))

        # Campos unificados para o frontend
        result["tx_power"]    = result.get("onu_tx_power")
        result["attenuation"] = result.get("attenuation_up") or result.get("attenuation_down")

        # Status de sinal
        rx = result.get("rx_power")
        if rx is not None:
            if rx >= -27:
                result["rx_status"] = "normal"
            elif rx > -29:
                result["rx_status"] = "warning"
            else:
                result["rx_status"] = "critical"

        return result

    def parse_discover_ports(self, output: str) -> List[Dict]:
        """
        Parseia output de show gpon onu state.
        Formato: gpon_olt-SLOT/CARD/PON ou indices SLOT/CARD/PON:ONU
        """
        ports = []
        seen = set()
        for line in output.split('\n'):
            m_onu = re.match(r'^(\d+)/(\d+)/(\d+):\d+', line)
            if m_onu:
                slot = int(m_onu.group(1))
                card = int(m_onu.group(2))
                pon = int(m_onu.group(3))
                key = (slot, card, pon)
                if key not in seen:
                    seen.add(key)
                    ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})
                continue
            m3 = re.search(r'gpon[_-]olt[_-](\d+)/(\d+)/(\d+)', line)
            if m3:
                slot = int(m3.group(1))
                card = int(m3.group(2))
                pon  = int(m3.group(3))
                key  = (slot, card, pon)
                if key not in seen:
                    seen.add(key)
                    ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})
                continue
        return ports


# ============================================================
# DRIVER ZTE C300 / C300M / C300T (Titan)
# ============================================================

class ZTEC300Driver(OLTDriver):
    """
    Driver para ZTE C300/C300M/C300T.
    Formato de interface: gpon-olt_SLOT/CARD/PON  |  gpon-onu_SLOT/CARD/PON:ID
    Mesmo formato do C320 (hífen antes do underline).
    Confirmado na OLT C300 ARAMARI: 'show gpon onu state gpon-olt_1/2/1' funciona.
    """
    model_key = "zte_c300"

    def olt_iface(self, slot: int, card: int, pon: int) -> str:
        return f"gpon-olt_{slot}/{card}/{pon}"

    def _onu_iface_parts(self, slot: int, card: int, pon: int, onu_id: int) -> str:
        return f"gpon-onu_{slot}/{card}/{pon}:{onu_id}"

    def cmd_onu_state(self, olt_iface: str) -> str:
        # C300 aceita sem interface (lista todas) ou com interface específica
        return f"show gpon onu state {olt_iface}"

    def cmd_onu_baseinfo(self, olt_iface: str) -> str:
        return f"show gpon onu baseinfo {olt_iface}"

    def cmd_onu_detail(self, onu_iface: str) -> str:
        return f"show gpon onu detail-info {onu_iface}"

    def cmd_olt_rx(self, olt_iface: str) -> str:
        return f"show pon power olt-rx {olt_iface}"

    def cmd_onu_power(self, onu_iface: str) -> str:
        return f"show pon power attenuation {onu_iface}"

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        """
        Sequência de comandos para reboot da ONU no C300/C610/Titan.
        Formato: gpon_onu-SLOT/CARD/PON:ID
        """
        return [
            f"pon-onu-mng {onu_iface}",
            "reboot",
            "y",
        ]

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        return f"show interface {onu_iface}"

    def parse_onu_traffic(self, output: str) -> dict:
        return _parse_onu_traffic_common(output)

    def cmd_discover_ports(self) -> List[str]:
        # Na C300/C610, 'show interface gpon_olt' sem iface específica retorna erro.
        # Usamos 'show gpon onu state' sem argumento para listar todas as ONUs
        # e depois derivamos as portas a partir dos índices retornados.
        return [
            "show gpon onu state",
        ]

    def parse_onu_state(self, output: str) -> List[Dict]:
        """
        Parseia: show gpon onu state [gpon_olt-SLOT/CARD/PON]
        Formato C300: 1/1/1:1   enable   enable   working   GPON
        (igual ao C320 mas com coluna Speed mode em vez de Channel)
        """
        onus = []
        seen = set()
        for line in output.split('\n'):
            line = line.strip()
            m = re.match(
                r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\w+)\s+(\w+)\s+(\w+)',
                line
            )
            if not m:
                continue
            idx = m.group(1)
            if idx in seen:
                continue
            seen.add(idx)
            oper = m.group(4).lower()
            color = (
                "green"  if oper == "working"   else
                "red"    if oper in ("dyinggasp", "los", "losi", "lof", "poweroff") else
                "yellow" if oper in ("reboot", "omci-down", "deactive") else
                "gray"
            )
            onus.append({
                "onu_index":       idx,
                "admin_state":     m.group(2).lower(),
                "omcc_state":      m.group(3).lower(),
                "oper_state":      m.group(4),
                "last_down_cause": None,
                "status_color":    color,
            })
        logger.debug(f"[PARSER] parse_onu_state (C300): {len(onus)} ONUs")
        return onus

    def parse_onu_baseinfo(self, output: str) -> List[Dict]:
        """
        Parseia: show gpon onu baseinfo gpon-olt_SLOT/CARD/PON
        Formato C300: gpon-onu_1/2/1:1    ZTE-F600    sn      SN:DACMED71A961    ready
        """
        onus = []
        seen = set()
        for line in output.split('\n'):
            line = line.strip()
            # Formato com prefixo gpon-onu_ (C300/C320) ou gpon_onu- (legado)
            m = re.match(
                r'^gpon[_-]onu[_-](\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+\S+\s+(\S+)',
                line
            )
            if m:
                idx    = m.group(1)
                model  = m.group(2)
                serial = m.group(3).replace("SN:", "").strip()
                if idx not in seen:
                    seen.add(idx)
                    onus.append({"onu_index": idx, "model": model, "serial": serial})
                continue
            # Fallback: sem prefixo
            m2 = re.match(
                r'^(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+(\S+)\s+(\S+)',
                line
            )
            if m2:
                idx    = m2.group(1)
                serial = m2.group(2)
                model  = m2.group(3)
                if idx not in seen:
                    seen.add(idx)
                    onus.append({"onu_index": idx, "model": model, "serial": serial})
        logger.debug(f"[PARSER] parse_onu_baseinfo (C300): {len(onus)} ONUs")
        return onus

    def parse_olt_rx(self, output: str) -> Dict[str, float]:
        """
        Parseia: show pon power olt-rx gpon-olt_SLOT/CARD/PON
        Formato C300: gpon-onu_1/2/1:1    -27.786(dbm)
        """
        rx_map = {}
        for line in output.split('\n'):
            line = line.strip()
            # Aceita ambos os formatos: gpon-onu_ e gpon_onu- (legado)
            m = re.match(r'gpon[_-]onu[_-](\d+/\d+(?:/\d+)?:\d+)\s+([-\d\.]+)', line)
            if m:
                idx = m.group(1)
                try:
                    rx_map[idx] = float(m.group(2))
                except ValueError:
                    pass
        logger.debug(f"[PARSER] parse_olt_rx (C300): {len(rx_map)} ONUs")
        return rx_map

    def parse_onu_detail(self, output: str) -> Dict:
        """
        Parseia: show gpon onu detail-info gpon_onu-SLOT/CARD/PON:ID
        Formato idêntico ao C320 nos campos principais.
        """
        # Reutiliza parser de campos comuns.
        return ZTEC600Driver().parse_onu_detail(output)

    def parse_onu_power(self, output: str) -> Dict:
        """
        Parseia: show pon power attenuation gpon_onu-SLOT/CARD/PON:ID
        Formato idêntico ao C320.
        """
        return ZTEC600Driver().parse_onu_power(output)

    def parse_discover_ports(self, output: str) -> List[Dict]:
        """
        Parseia output de 'show gpon onu state' (sem argumento) para descobrir portas.
        O C300/C610 não suporta 'show interface gpon_olt' sem especificar a interface.
        Extrai as portas únicas a partir dos índices de ONU (SLOT/CARD/PON:ID).

        Também aceita output de 'show interface gpon-olt_X/X/X' com múltiplas interfaces.
        """
        ports = []
        seen = set()

        for line in output.split('\n'):
            line = line.strip()

            # Extrai porta de índice de ONU: SLOT/CARD/PON:ID
            m_onu = re.match(r'^(\d+)/(\d+)/(\d+):\d+', line)
            if m_onu:
                slot = int(m_onu.group(1))
                card = int(m_onu.group(2))
                pon  = int(m_onu.group(3))
                key  = (slot, card, pon)
                if key not in seen:
                    seen.add(key)
                    ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})
                continue

            # Formato explícito: gpon-olt_SLOT/CARD/PON ou gpon_olt-SLOT/CARD/PON (legado)
            m3 = re.search(r'gpon[_-]olt[_-](\d+)/(\d+)/(\d+)', line)
            if m3:
                slot = int(m3.group(1))
                card = int(m3.group(2))
                pon  = int(m3.group(3))
                key  = (slot, card, pon)
                if key not in seen:
                    seen.add(key)
                    ports.append({"slot": slot, "card": card, "pon": pon, "port_type": "gpon"})

        logger.debug(f"[PARSER] parse_discover_ports (C300): {len(ports)} portas")
        return ports

    def parse_onu_state_for_discover(self, output: str, slot: int, card: int, pon: int) -> bool:
        """
        Na C300/C610, 'show gpon onu state gpon_olt-S/C/P' retorna:
          - Cabeçalho + linhas de ONUs: porta válida com ONUs
          - Apenas cabeçalho (sem ONUs): porta válida mas vazia
          - Mensagem de erro/invalid: porta não existe

        Aceita a porta somente se:
          1. Não contiver nenhum indicador de erro
          2. Tiver linhas de ONU OU cabeçalho de tabela
        """
        out = output.strip()
        if not out:
            return False
        out_lower = out.lower()
        # Rejeita se contiver qualquer indicador de erro
        # Inclui '%' sozinho para capturar '%Error', '% Error', etc.
        error_indicators = (
            "invalid input",
            "invalid command",
            "invalid parameter",
            "error",
            "not exist",
            "no such",
            "^",
        )
        if any(ind in out_lower for ind in error_indicators):
            return False
        if "%" in out:
            return False
        # Aceita SOMENTE se tiver cabeçalho de tabela OU linhas de ONU
        # Isso evita aceitar outputs ambíguos (ex: prompt vazio, mensagens genéricas)
        iface_prefix = f"{slot}/{card}/{pon}:"
        has_onus = any(line.strip().startswith(iface_prefix) for line in out.split('\n'))
        has_header = "onuindex" in out_lower or "admin state" in out_lower
        return has_onus or has_header


# ============================================================
# DRIVER PARKS 3000/4000
# ============================================================

class Parks30004000Driver(OLTDriver):
    """
    Driver para Parks 3000/4000.
    Formato da OLT: gponSLOT/PON. Internamente mantemos SLOT/1/PON:ONU
    para reaproveitar telas, cache e filtros existentes.
    """
    model_key = "parks_3000_4000"

    def olt_iface(self, slot: int, card: int, pon: int) -> str:
        return f"gpon{slot}/{pon}"

    def _onu_iface_parts(self, slot: int, card: int, pon: int, onu_id: int) -> str:
        return f"gpon{slot}/{pon} onu {onu_id}"

    def _iface_from_onu_ref(self, onu_iface: str) -> str:
        m = re.search(r'(gpon\d+/\d+)\s+onu\s+\d+', onu_iface, re.IGNORECASE)
        return m.group(1) if m else onu_iface

    def _onu_id_from_ref(self, onu_iface: str) -> Optional[int]:
        m = re.search(r'\bonu\s+(\d+)\b', onu_iface, re.IGNORECASE)
        return int(m.group(1)) if m else None

    def cmd_onu_state(self, olt_iface: str) -> str:
        return f"show interface {olt_iface} onu status"

    def cmd_onu_baseinfo(self, olt_iface: str) -> str:
        return f"show interface {olt_iface} onu model"

    def cmd_onu_detail(self, onu_iface: str) -> str:
        return f"show interface {onu_iface} information"

    def cmd_olt_rx(self, olt_iface: str) -> str:
        return f"show interface {olt_iface} onu status"

    def cmd_onu_power(self, onu_iface: str) -> str:
        return f"show interface {onu_iface} status"

    def cmd_onu_service(self, onu_iface: str) -> str:
        return f"show interface {onu_iface}"

    def cmd_onu_equip(self, onu_iface: str) -> str:
        return f"show interface {onu_iface} information"

    def cmd_optical_module(self, olt_iface: str) -> str:
        return f"show interface {olt_iface} sfp"

    def cmd_onu_reboot(self, onu_iface: str) -> List[str]:
        iface = self._iface_from_onu_ref(onu_iface)
        onu_id = self._onu_id_from_ref(onu_iface)
        return ["configure terminal", f"interface {iface}", f"onu {onu_id} reset", "end"]

    def cmd_onu_traffic(self, onu_iface: str) -> str:
        return f"show interface {onu_iface}"

    def cmd_discover_ports(self) -> List[str]:
        return ["show interface gpon"]

    def cmd_backup_to_ftp(
        self,
        server_ip: str,
        filename: str,
        ftp_user: str,
        ftp_password: str,
        source_path: str,
    ) -> str:
        return f"copy running-config ftp://{server_ip}/{filename} {ftp_user}"

    def cmd_uncfg_onus(self) -> str:
        return "show gpon onu unconfigured"

    def _idx_from_iface(self, iface: str, onu_id: int) -> str:
        m = re.search(r'gpon(\d+)/(\d+)', iface or "", re.IGNORECASE)
        if not m:
            return f"1/1/1:{onu_id}"
        return f"{int(m.group(1))}/1/{int(m.group(2))}:{onu_id}"

    def _power_value(self, value: str) -> Optional[float]:
        if not value or "no signal" in value.lower():
            return None
        m = re.search(r'(-?\d+(?:\.\d+)?)\s*dB', value, re.IGNORECASE)
        if not m:
            return None
        try:
            return float(m.group(1))
        except ValueError:
            return None

    def _rx_status(self, value: Optional[float]) -> Optional[str]:
        if value is None:
            return None
        if value >= -27:
            return "normal"
        if value > -29:
            return "warning"
        return "critical"

    def parse_onu_state(self, output: str) -> List[Dict]:
        onus = []
        iface_match = re.search(r'Interface\s+(gpon\d+/\d+)\s*:', output, re.IGNORECASE)
        iface = iface_match.group(1) if iface_match else ""
        block_re = re.compile(
            r'^\s*(\d+)-([A-Za-z0-9]+):\s*$(.*?)(?=^\s*\d+-[A-Za-z0-9]+:\s*$|\Z)',
            re.IGNORECASE | re.MULTILINE | re.DOTALL,
        )
        for match in block_re.finditer(output):
            onu_id = int(match.group(1))
            serial = match.group(2).strip().upper()
            body = match.group(3)
            status_m = re.search(r'Status\s*:\s*(.+)', body, re.IGNORECASE)
            power_m = re.search(r'Power Level\s*:\s*(.+)', body, re.IGNORECASE)
            rssi_m = re.search(r'RSSI\s*:\s*(.+)', body, re.IGNORECASE)
            status = (status_m.group(1).strip() if status_m else "UNKNOWN")
            power = self._power_value(power_m.group(1).strip() if power_m else "")
            rssi = self._power_value(rssi_m.group(1).strip() if rssi_m else "")
            oper = "working" if "active" in status.lower() else "offline"
            no_signal = (
                (power_m and "no signal" in power_m.group(1).lower()) or
                (rssi_m and "no signal" in rssi_m.group(1).lower())
            )
            last_down = "NO SIGNAL" if no_signal else (status if oper != "working" else None)
            onus.append({
                "onu_index": self._idx_from_iface(iface, onu_id),
                "serial": serial,
                "admin_state": "enable" if oper == "working" else "disable",
                "omcc_state": status,
                "oper_state": oper,
                "last_down_cause": last_down,
                "status_color": "green" if oper == "working" else "red",
                "rx_power": power,
                "onu_rx_power": power,
                "rx_status": self._rx_status(power),
                "olt_rx_power": rssi,
                "olt_rx_status": self._rx_status(rssi),
            })
        logger.debug(f"[PARSER] parse_onu_state (Parks): {len(onus)} ONUs")
        return onus

    def parse_onu_baseinfo(self, output: str) -> List[Dict]:
        onus = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.lower().startswith("serial") or line.startswith("-"):
                continue
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 2 and re.search(r'[A-Za-z]{4}[A-Fa-f0-9]+', parts[0]):
                    onus.append({"serial": parts[0].upper(), "model": parts[1]})
                continue
            m = re.match(r'([A-Za-z]{4}[A-Fa-f0-9]+)\s+(.+)$', line)
            if m:
                onus.append({"serial": m.group(1).upper(), "model": m.group(2).strip()})
        logger.debug(f"[PARSER] parse_onu_baseinfo (Parks): {len(onus)} ONUs")
        return onus

    def parse_olt_rx(self, output: str) -> Dict[str, float]:
        result = {}
        for onu in self.parse_onu_state(output):
            value = onu.get("olt_rx_power")
            if value is not None:
                result[onu["onu_index"]] = value
        return result

    def parse_onu_detail(self, output: str) -> Dict:
        result = {}
        iface_m = re.search(r'ONU Status for ONU\s+(\d+)', output, re.IGNORECASE)
        if iface_m:
            result["onu_number"] = iface_m.group(1)
        fields = {
            "state": r'ONU primary status\s*:\s*(.+)',
            "phase_state": r'ONU Protection Mode\s*:\s*(.+)',
            "secondary_status": r'ONU secondary status\s*:\s*(.+)',
            "vendor_id": r'Serial Number \(vendor ID\)\s*:\s*(.+)',
            "vendor_specific": r'Serial Number \(vendor Specific\)\s*:\s*(.+)',
        }
        for key, pattern in fields.items():
            m = re.search(pattern, output, re.IGNORECASE)
            if m:
                result[key] = " ".join(m.group(1).strip().split())
        if result.get("vendor_id") or result.get("vendor_specific"):
            vendor = (result.get("vendor_id") or "").replace(" ", "")
            specific = (result.get("vendor_specific") or "").replace(" ", "")
            result["serial"] = f"{vendor}{specific}".upper()
            result["vendor"] = vendor
        if result.get("state"):
            result["admin_state"] = "enable" if "active" in result["state"].lower() else "disable"
        result.setdefault("history", [])
        return result

    def parse_onu_power(self, output: str) -> Dict:
        states = self.parse_onu_state(output)
        if states:
            onu = states[0]
            return {
                "rx_power": onu.get("rx_power"),
                "onu_rx_power": onu.get("rx_power"),
                "olt_rx_power": onu.get("olt_rx_power"),
                "rx_status": onu.get("rx_status"),
                "olt_rx_status": onu.get("olt_rx_status"),
            }
        return {}

    def parse_onu_traffic(self, output: str) -> dict:
        return {}

    def parse_discover_ports(self, output: str) -> List[Dict]:
        ports = []
        seen = set()
        for m in re.finditer(r'\bgpon(\d+)/(\d+)\b', output, re.IGNORECASE):
            slot = int(m.group(1))
            pon = int(m.group(2))
            key = (slot, 1, pon)
            if key not in seen:
                seen.add(key)
                ports.append({"slot": slot, "card": 1, "pon": pon, "port_type": "gpon"})
        return ports

    def parse_onu_state_for_discover(self, output: str, slot: int, card: int, pon: int) -> bool:
        out = output.strip()
        if not out:
            return False
        lower = out.lower()
        if any(token in lower for token in ("invalid", "unknown", "not exist", "no such", "error")) or "%" in out:
            return False
        return f"interface gpon{slot}/{pon}" in lower or bool(re.search(r'^\s*\d+-[A-Za-z0-9]+:', out, re.MULTILINE))

    def parse_uncfg_onus(self, output: str) -> List[Dict]:
        onus = []
        for line in output.splitlines():
            line = line.strip()
            if not line or line.startswith("-") or line.lower().startswith("interface"):
                continue
            if "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 3 and parts[0].lower().startswith("gpon"):
                    iface = parts[0]
                    serial = parts[1].upper()
                    model = parts[2]
                    m = re.search(r'gpon(\d+)/(\d+)', iface, re.IGNORECASE)
                    idx = f"{int(m.group(1))}/1/{int(m.group(2))}" if m else iface
                    onus.append({
                        "onu_index": idx,
                        "olt_index": iface,
                        "model": model,
                        "serial": serial,
                        "password": "",
                    })
                continue
            m = re.match(r'(gpon\d+/\d+)\s+(\S+)\s+(.+)$', line, re.IGNORECASE)
            if m:
                iface = m.group(1)
                mi = re.search(r'gpon(\d+)/(\d+)', iface, re.IGNORECASE)
                idx = f"{int(mi.group(1))}/1/{int(mi.group(2))}" if mi else iface
                onus.append({
                    "onu_index": idx,
                    "olt_index": iface,
                    "model": m.group(3).strip(),
                    "serial": m.group(2).upper(),
                    "password": "",
                })
        return onus


# ============================================================
# REGISTRO DE DRIVERS
# ============================================================

DRIVERS: Dict[str, OLTDriver] = {
    "zte_c600": ZTEC600Driver(),
    "zte_c300": ZTEC300Driver(),
    "parks_3000_4000": Parks30004000Driver(),
    # Compatibilidade com registros antigos criados antes da separacao C600/C300.
    "zte_c320": ZTEC600Driver(),
}

# Driver padrão (retrocompatibilidade)
DEFAULT_DRIVER = DRIVERS["zte_c600"]


def get_driver(model_key: Optional[str]) -> OLTDriver:
    """Retorna o driver correto para o modelo da OLT."""
    if not model_key:
        return DEFAULT_DRIVER
    return DRIVERS.get(model_key, DEFAULT_DRIVER)


def detect_model(login_banner: str) -> str:
    """
    Tenta detectar o modelo da OLT pelo banner de login.
    Retorna a chave do modelo (ex: 'zte_c300') ou 'zte_c600' como padrão.

    Regra principal:
      - Banner contém "C600" ou "TITAN series" → zte_c600
      - Banner contém "C300" → zte_c300
    """
    banner_lower = login_banner.lower()

    if "parks" in banner_lower or "fiberlink" in banner_lower or "gpon switch port" in banner_lower:
        return "parks_3000_4000"

    if "c300" in banner_lower:
        return "zte_c300"

    if "c600" in banner_lower or "titan series" in banner_lower:
        return "zte_c600"

    # Detecta pelo formato da interface presente no output
    if "gpon_olt-" in login_banner or "gpon_onu-" in login_banner:
        return "zte_c600"
    if "gpon-olt_" in login_banner or "gpon-onu_" in login_banner:
        return "zte_c300"
    if re.search(r'\bgpon\d+/\d+\b', login_banner, re.IGNORECASE):
        return "parks_3000_4000"

    return "zte_c600"
