"""
OLT Client para ZTE Titan (C600/C610/C620/C650)
Sintaxe de interface conforme manual: gpon-olt_SLOT/PON (2 partes)
Exemplos: gpon-olt_1/1, gpon-olt_1/2, gpon-olt_2/1 ...
"""
import re
import time
import socket
import paramiko
from typing import Optional, List, Dict, Any, Tuple
from .config import settings


class OLTConnectionError(Exception):
    pass


# ============================================================
# Implementação própria de Telnet (telnetlib foi removido no Python 3.13)
# ============================================================
class SimpleTelnet:
    """Cliente Telnet simples via socket puro, compatível com Python 3.13+."""

    IAC  = bytes([255])
    DONT = bytes([254])
    DO   = bytes([253])
    WONT = bytes([252])
    WILL = bytes([251])

    def __init__(self, host: str, port: int, timeout: int = 30):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock = None
        self._buf = b""

    def open(self):
        self.sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self.sock.settimeout(self.timeout)

    def _recv_raw(self, size: int = 4096) -> bytes:
        try:
            return self.sock.recv(size)
        except socket.timeout:
            return b""

    def _process_iac(self, data: bytes) -> bytes:
        """Remove negociações IAC do stream Telnet."""
        out = b""
        i = 0
        while i < len(data):
            if data[i:i+1] == self.IAC:
                if i + 2 < len(data):
                    cmd = data[i+1:i+2]
                    opt = data[i+2:i+3]
                    if cmd == self.DO:
                        self.sock.sendall(self.IAC + self.WONT + opt)
                    elif cmd == self.WILL:
                        self.sock.sendall(self.IAC + self.DONT + opt)
                    i += 3
                else:
                    i += 1
            else:
                out += data[i:i+1]
                i += 1
        return out

    def read_until(self, expected: bytes, timeout: int = 10) -> bytes:
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self._recv_raw()
            if raw:
                self._buf += self._process_iac(raw)
            if expected in self._buf:
                idx = self._buf.index(expected) + len(expected)
                result = self._buf[:idx]
                self._buf = self._buf[idx:]
                return result
            time.sleep(0.1)
        return self._buf

    def read_very_eager(self) -> bytes:
        self.sock.settimeout(0.3)
        try:
            data = b""
            while True:
                chunk = self._recv_raw(4096)
                if not chunk:
                    break
                data += self._process_iac(chunk)
            return data
        except Exception:
            return b""
        finally:
            self.sock.settimeout(self.timeout)

    def write(self, data: bytes):
        self.sock.sendall(data)

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# ============================================================
# CLIENTES SSH E TELNET
# ============================================================

class OLTSSHClient:
    """Cliente SSH para comunicação com OLTs ZTE Titan."""

    def __init__(self, ip: str, port: int, username: str, password: str):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.client = None
        self.shell = None

    def connect(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(
                self.ip, port=self.port,
                username=self.username, password=self.password,
                timeout=settings.SSH_TIMEOUT,
                look_for_keys=False, allow_agent=False
            )
            self.shell = self.client.invoke_shell(width=250, height=50)
            time.sleep(1)
            # Consome o banner inicial
            if self.shell.recv_ready():
                self.shell.recv(8192)
            # Desabilita paginação
            self._send_raw("terminal length 0\n")
            time.sleep(0.5)
            if self.shell.recv_ready():
                self.shell.recv(8192)
        except OLTConnectionError:
            raise
        except Exception as e:
            raise OLTConnectionError(f"Falha ao conectar via SSH em {self.ip}:{self.port} - {str(e)}")

    def _send_raw(self, cmd: str):
        self.shell.send(cmd)
        time.sleep(0.3)

    def execute_command(self, command: str, timeout: int = None) -> str:
        if not self.shell:
            raise OLTConnectionError("Não conectado à OLT")

        timeout = timeout or settings.SSH_COMMAND_TIMEOUT
        self.shell.send(command + "\n")
        time.sleep(0.5)

        output = ""
        self.shell.settimeout(timeout)
        start_time = time.time()

        try:
            while time.time() - start_time < timeout:
                if self.shell.recv_ready():
                    chunk = self.shell.recv(8192).decode("utf-8", errors="replace")
                    output += chunk
                    if re.search(r'--\s*[Mm]ore\s*--', chunk):
                        self.shell.send(" ")
                        time.sleep(0.2)
                        continue
                    if re.search(r'[>#]\s*$', chunk.strip()):
                        break
                else:
                    time.sleep(0.1)
                    if not self.shell.recv_ready():
                        time.sleep(0.3)
                        if not self.shell.recv_ready():
                            break
        except socket.timeout:
            pass

        # Limpa saída: remove o eco do comando e linhas de prompt
        lines = output.split('\n')
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.endswith(command.strip()):
                if not re.match(r'^[\w\-\.]+[#>]\s*$', stripped):
                    clean_lines.append(line.rstrip())

        return '\n'.join(clean_lines)

    def disconnect(self):
        try:
            if self.shell:
                self.shell.close()
            if self.client:
                self.client.close()
        except Exception:
            pass


class OLTTelnetClient:
    """Cliente Telnet para comunicação com OLTs ZTE Titan."""

    def __init__(self, ip: str, port: int, username: str, password: str):
        self.ip = ip
        self.port = port
        self.username = username
        self.password = password
        self.tn = None

    def connect(self):
        try:
            self.tn = SimpleTelnet(self.ip, self.port, timeout=settings.SSH_TIMEOUT)
            self.tn.open()
            self.tn.read_until(b"Username:", timeout=15)
            self.tn.write(self.username.encode("ascii") + b"\n")
            self.tn.read_until(b"Password:", timeout=10)
            self.tn.write(self.password.encode("ascii") + b"\n")
            output = self.tn.read_until(b"#", timeout=15).decode("utf-8", errors="replace")
            if "#" not in output and ">" not in output:
                raise OLTConnectionError("Login Telnet falhou - prompt não encontrado")
            # Desabilita paginação
            self.tn.write(b"terminal length 0\n")
            time.sleep(0.5)
            self.tn.read_very_eager()
        except OLTConnectionError:
            raise
        except Exception as e:
            raise OLTConnectionError(f"Falha ao conectar via Telnet em {self.ip}:{self.port} - {str(e)}")

    def execute_command(self, command: str, timeout: int = None) -> str:
        timeout = timeout or settings.SSH_COMMAND_TIMEOUT
        self.tn.write(command.encode("ascii") + b"\n")
        output = ""
        start = time.time()
        while time.time() - start < timeout:
            try:
                chunk = self.tn.read_very_eager().decode("utf-8", errors="replace")
                if chunk:
                    output += chunk
                    if re.search(r'[>#]\s*$', chunk.strip()):
                        break
                    if re.search(r'--\s*[Mm]ore\s*--', chunk):
                        self.tn.write(b" ")
                        time.sleep(0.2)
                else:
                    time.sleep(0.2)
            except EOFError:
                break
        return output

    def disconnect(self):
        try:
            if self.tn:
                self.tn.close()
        except Exception:
            pass


def get_olt_client(ip: str, port: int, username: str, password: str, protocol: str):
    """Factory para criar o cliente correto baseado no protocolo."""
    if protocol.lower() == "ssh":
        return OLTSSHClient(ip, port, username, password)
    elif protocol.lower() == "telnet":
        return OLTTelnetClient(ip, port, username, password)
    else:
        raise ValueError(f"Protocolo não suportado: {protocol}")


# ============================================================
# PARSERS DE SAÍDA DOS COMANDOS ZTE TITAN
# Sintaxe: gpon-olt_SLOT/PON  (2 partes, conforme manual)
# Exemplos: gpon-olt_1/1, gpon-olt_1/2, gpon-olt_2/8
# ============================================================

def parse_onu_state(output: str) -> List[Dict]:
    """
    Parseia: show gpon onu state gpon-olt_SLOT/PON
    Formato esperado de cada linha:
      SLOT/PON:ONU_ID   admin_state   oper_state   last_down_cause
    Ex: 1/1:1   enable   working   -
        1/2:85  enable   disable   LOS
    """
    onus = []
    lines = output.split('\n')
    for line in lines:
        line = line.strip()
        # Aceita índices no formato SLOT/PON:ID
        match = re.match(
            r'(\d+/\d+:\d+)\s+(enable|disable)\s+(working|disable|initial|ranging|standby|unknown)\s*(\S*)',
            line
        )
        if match:
            onu_index = match.group(1)
            admin_state = match.group(2)
            oper_state = match.group(3)
            last_down = match.group(4) if match.group(4) and match.group(4) != '-' else None

            if oper_state == "working":
                color = "green"
            elif oper_state in ("initial", "ranging"):
                color = "yellow"
            else:
                color = "red"

            onus.append({
                "onu_index": onu_index,
                "admin_state": admin_state,
                "oper_state": oper_state,
                "last_down_cause": last_down,
                "status_color": color
            })
    return onus


def parse_onu_detail(output: str, onu_index: str) -> Dict:
    """Parseia: show gpon onu detail-info gpon-onu_SLOT/PON:ID"""
    result = {"onu_index": onu_index}
    patterns = {
        "serial_number": r'[Ss]erial\s*[Nn]umber\s*[:\-]\s*(\S+)',
        "vendor_id": r'[Vv]endor\s*[Ii][Dd]\s*[:\-]\s*(\S+)',
        "onu_type": r'ONU\s*[Tt]ype\s*[:\-]\s*(\S+)',
        "run_state": r'[Rr]un\s*[Ss]tate\s*[:\-]\s*(\S+)',
        "omci_state": r'OMCI\s*[Ss]tate\s*[:\-]\s*(\S+)',
        "online_time": r'[Oo]nline\s*[Tt]ime\s*[:\-]\s*(.+)',
        "last_down_cause": r'[Ll]ast\s*[Dd]own\s*[Cc]ause\s*[:\-]\s*(\S+)',
        "fec": r'FEC\s*[:\-]\s*(\S+)',
        "dba": r'DBA\s*[:\-]\s*(\S+)',
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, output)
        if m:
            result[key] = m.group(1).strip()
    return result


def parse_onu_power(output: str, onu_index: str) -> Dict:
    """Parseia: show pon power attenuation gpon-onu_SLOT/PON:ID"""
    result = {"onu_index": onu_index}

    rx_match = re.search(r'[Rr][Xx]\s*[Pp]ower\s*[:\-]?\s*([-\d\.]+)\s*dBm', output)
    tx_match = re.search(r'[Tt][Xx]\s*[Pp]ower\s*[:\-]?\s*([-\d\.]+)\s*dBm', output)
    att_match = re.search(r'[Aa]ttenuation\s*[:\-]?\s*([-\d\.]+)\s*dBm', output)

    if rx_match:
        rx = float(rx_match.group(1))
        result["rx_power"] = rx
        if rx >= -27:
            result["rx_status"] = "normal"
        elif rx >= -29:
            result["rx_status"] = "warning"
        else:
            result["rx_status"] = "critical"
    else:
        result["rx_status"] = "unknown"

    if tx_match:
        result["tx_power"] = float(tx_match.group(1))
    if att_match:
        result["attenuation"] = float(att_match.group(1))

    return result


def parse_olt_rx_power(output: str) -> List[Dict]:
    """Parseia: show pon power olt-rx gpon-olt_SLOT/PON"""
    results = []
    lines = output.split('\n')
    for line in lines:
        # Aceita SLOT/PON:ID ou SLOT/CARD/PORT:ID
        match = re.match(r'(\d+/\d+(?:/\d+)?:\d+)\s+([-\d\.]+)\s*dBm', line.strip())
        if match:
            onu_index = match.group(1)
            power = float(match.group(2))
            if power >= -25:
                status = "normal"
            elif power >= -28:
                status = "warning"
            else:
                status = "critical"
            results.append({
                "onu_index": onu_index,
                "olt_rx_power": power,
                "olt_rx_status": status
            })
    return results


def parse_onu_distance(output: str, onu_index: str) -> Dict:
    """Parseia: show gpon onu distance gpon-onu_SLOT/PON:ID"""
    result = {"onu_index": onu_index}
    match = re.search(r'[Dd]istance\s*[:\-]?\s*(\d+)\s*m', output)
    if match:
        result["distance_m"] = int(match.group(1))
    return result


def parse_onu_wan(output: str, onu_index: str) -> Dict:
    """Parseia: show gpon remote-onu wan-info gpon-onu_SLOT/PON:ID"""
    result = {"onu_index": onu_index}
    patterns = {
        "connection_type": r'[Cc]onnection\s*[Tt]ype\s*[:\-]\s*(\S+)',
        "status": r'[Ss]tatus\s*[:\-]\s*(Connected|Connecting|Disconnected|connected|connecting|disconnected)',
        "ip_address": r'IP\s*[Aa]ddress\s*[:\-]\s*(\d+\.\d+\.\d+\.\d+)',
        "gateway": r'[Gg]ateway\s*[:\-]\s*(\d+\.\d+\.\d+\.\d+)',
        "dns": r'DNS\s*[:\-]\s*(\d+\.\d+\.\d+\.\d+)',
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, output)
        if m:
            result[key] = m.group(1).strip()
    return result


def parse_onu_voip(output: str, onu_index: str) -> Dict:
    """Parseia: show gpon remote-onu voip-status gpon-onu_SLOT/PON:ID"""
    result = {"onu_index": onu_index}
    match = re.search(r'(Registered|Unregistered|Failed|registered|unregistered|failed)', output)
    if match:
        result["status"] = match.group(1)
    return result


def parse_onu_temperature(output: str, onu_index: str) -> Dict:
    """Parseia temperatura da ONU."""
    result = {"onu_index": onu_index}
    match = re.search(r'[Tt]emperature\s*[:\-]?\s*([\d\.]+)\s*[°C]?C?', output)
    if match:
        temp = float(match.group(1))
        result["temperature"] = temp
        if temp <= 70:
            result["temp_status"] = "normal"
        elif temp <= 80:
            result["temp_status"] = "warning"
        else:
            result["temp_status"] = "critical"
    else:
        result["temp_status"] = "unknown"
    return result


def parse_onu_firmware(output: str, onu_index: str) -> Dict:
    """Parseia firmware da ONU."""
    result = {"onu_index": onu_index}
    patterns = {
        "current_version": r'[Cc]urrent\s*[Vv]ersion\s*[:\-]\s*(\S+)',
        "active_version": r'[Aa]ctive\s*[Vv]ersion\s*[:\-]\s*(\S+)',
        "backup_version": r'[Bb]ackup\s*[Vv]ersion\s*[:\-]\s*(\S+)',
    }
    for key, pattern in patterns.items():
        m = re.search(pattern, output)
        if m:
            result[key] = m.group(1).strip()
    return result


def parse_onu_baseinfo(output: str) -> List[Dict]:
    """
    Parseia: show gpon onu baseinfo gpon-olt_SLOT/PON
    Formato: SLOT/PON:ID   SERIAL   MODEL   admin_state   oper_state
    Ex: 1/2:85   ZTEG12345678   F601   enable   working
    """
    onus = []
    lines = output.split('\n')
    for line in lines:
        line = line.strip()
        # Aceita SLOT/PON:ID ou SLOT/CARD/PORT:ID
        match = re.match(r'(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s*(\S*)', line)
        if match and ':' in match.group(1):
            onus.append({
                "onu_index": match.group(1),
                "serial": match.group(2),
                "model": match.group(3),
                "admin_state": match.group(4),
                "oper_state": match.group(5) if match.group(5) else "unknown"
            })
    return onus


def parse_uncfg_onus(output: str) -> List[Dict]:
    """Parseia: show gpon onu uncfg"""
    onus = []
    lines = output.split('\n')
    for line in lines:
        line = line.strip()
        # gpon-onu_1/2:85  ou  gpon-onu_1/2/3:85
        match = re.match(r'gpon-onu_(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)', line)
        if match:
            onus.append({
                "onu_index": match.group(1),
                "serial": match.group(2)
            })
        else:
            # Formato sem prefixo: 1/2:85  SERIAL
            match2 = re.match(r'(\d+/\d+(?:/\d+)?:\d+)\s+(\S+)', line)
            if match2:
                onus.append({
                    "onu_index": match2.group(1),
                    "serial": match2.group(2)
                })
    return onus


def parse_olt_ports(output: str) -> List[Dict]:
    """
    Parseia interfaces gpon-olt da OLT.
    Aceita formato de 2 partes: gpon-olt_SLOT/PON
    Aceita formato de 3 partes: gpon-olt_SLOT/CARD/PORT (compatibilidade)
    """
    ports = []
    seen = set()
    lines = output.split('\n')
    for line in lines:
        # Formato 2 partes: gpon-olt_1/2
        m2 = re.search(r'gpon-olt_(\d+)/(\d+)(?!\s*/\s*\d)', line)
        # Formato 3 partes: gpon-olt_1/2/3
        m3 = re.search(r'gpon-olt_(\d+)/(\d+)/(\d+)', line)

        if m3:
            slot = int(m3.group(1))
            pon = int(m3.group(3))  # usa a última parte como PON
            key = (slot, pon)
            if key not in seen:
                seen.add(key)
                ports.append({
                    "slot": slot,
                    "pon": pon,
                    "port_type": "gpon",
                    "description": line.strip()
                })
        elif m2:
            slot = int(m2.group(1))
            pon = int(m2.group(2))
            key = (slot, pon)
            if key not in seen:
                seen.add(key)
                ports.append({
                    "slot": slot,
                    "pon": pon,
                    "port_type": "gpon",
                    "description": line.strip()
                })
    return ports


def parse_software_version(output: str) -> Dict:
    """Parseia a versão do software da OLT."""
    result = {}
    version_match = re.search(r'[Vv]ersion\s*[:\-]\s*(\S+)', output)
    model_match = re.search(r'(C600|C610|C620|C650)', output)
    if version_match:
        result["firmware"] = version_match.group(1)
    if model_match:
        result["model"] = f"ZTE Titan {model_match.group(1)}"
    return result


# ============================================================
# FUNÇÕES DE ALTO NÍVEL
# ============================================================

def _olt_iface(slot: int, pon: int) -> str:
    """Gera referência de interface OLT: gpon-olt_SLOT/PON"""
    return f"gpon-olt_{slot}/{pon}"


def _onu_iface(slot: int, pon: int, onu_id: int) -> str:
    """Gera referência de ONU: gpon-onu_SLOT/PON:ONU_ID"""
    return f"gpon-onu_{slot}/{pon}:{onu_id}"


def test_olt_connection(ip: str, port: int, username: str, password: str, protocol: str) -> Tuple[bool, str]:
    """Testa a conexão com uma OLT. Retorna (sucesso, mensagem)."""
    client = None
    try:
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()
        output = client.execute_command("show software")
        return True, output
    except OLTConnectionError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Erro inesperado: {str(e)}"
    finally:
        if client:
            client.disconnect()


def discover_olt_ports(ip: str, port: int, username: str, password: str, protocol: str) -> List[Dict]:
    """
    Descobre as portas PON disponíveis na OLT.
    Sintaxe ZTE Titan: gpon-olt_SLOT/PON (2 partes)
    Estratégia:
    1. Tenta 'show interface gpon-olt' para listar todas de uma vez
    2. Varredura: slots 1-4, PON 1-16
    """
    client = None
    try:
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        ports = []
        seen = set()

        # --- Estratégia 1: listar todas as interfaces de uma vez ---
        for cmd in ["show interface gpon-olt", "show running-config interface gpon-olt"]:
            output = client.execute_command(cmd)
            found = parse_olt_ports(output)
            for p in found:
                key = (p["slot"], p["pon"])
                if key not in seen:
                    seen.add(key)
                    ports.append(p)
            if ports:
                break

        # --- Estratégia 2: varredura slot 1-4, PON 1-16 ---
        for slot in range(1, 5):
            for pon in range(1, 17):
                key = (slot, pon)
                if key in seen:
                    continue
                iface = _olt_iface(slot, pon)
                try:
                    out = client.execute_command(
                        f"show gpon onu state {iface}",
                        timeout=8
                    )
                    # Porta válida: tem ONUs ou retornou sem erro
                    has_onus = bool(
                        re.search(r'\d+/\d+:\d+', out) or
                        "working" in out.lower() or
                        "disable" in out.lower() or
                        "initial" in out.lower()
                    )
                    is_valid = has_onus or (
                        out.strip() != "" and
                        "invalid" not in out.lower() and
                        "error" not in out.lower() and
                        "not exist" not in out.lower() and
                        "no such" not in out.lower() and
                        "%" not in out  # ZTE usa % para erros
                    )
                    if is_valid:
                        seen.add(key)
                        ports.append({
                            "slot": slot,
                            "pon": pon,
                            "port_type": "gpon",
                            "description": iface
                        })
                except Exception:
                    pass

        # Ordena por slot, pon
        ports.sort(key=lambda x: (x["slot"], x["pon"]))
        return ports

    except Exception as e:
        raise OLTConnectionError(f"Falha na descoberta: {str(e)}")
    finally:
        if client:
            client.disconnect()


def get_pon_onu_status(ip: str, port: int, username: str, password: str, protocol: str,
                       slot: int, pon: int) -> List[Dict]:
    """Obtém o status de todas as ONUs de uma porta PON."""
    client = None
    try:
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()
        iface = _olt_iface(slot, pon)
        output = client.execute_command(f"show gpon onu state {iface}")
        client.disconnect()
        return parse_onu_state(output)
    except OLTConnectionError:
        raise
    except Exception as e:
        raise OLTConnectionError(f"Erro ao consultar ONUs: {str(e)}")
    finally:
        if client:
            try:
                client.disconnect()
            except Exception:
                pass
