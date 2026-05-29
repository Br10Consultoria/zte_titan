import re
import time
import socket
import telnetlib
import paramiko
from typing import Optional, List, Dict, Any, Tuple
from .config import settings


class OLTConnectionError(Exception):
    pass


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
                hostname=self.ip,
                port=self.port,
                username=self.username,
                password=self.password,
                timeout=settings.SSH_TIMEOUT,
                look_for_keys=False,
                allow_agent=False
            )
            # Abre um shell interativo para suportar paginação
            self.shell = self.client.invoke_shell(width=250, height=50)
            time.sleep(1)
            self._read_until_prompt()
        except Exception as e:
            raise OLTConnectionError(f"Falha ao conectar via SSH em {self.ip}:{self.port} - {str(e)}")

    def _read_until_prompt(self, timeout: int = 10) -> str:
        """Lê a saída até encontrar um prompt ou timeout."""
        output = ""
        self.shell.settimeout(timeout)
        try:
            while True:
                chunk = self.shell.recv(4096).decode("utf-8", errors="replace")
                output += chunk
                # Detecta prompts comuns da ZTE Titan
                if re.search(r'[>#]\s*$', chunk.strip()):
                    break
                if "More" in chunk or "--More--" in chunk:
                    self.shell.send(" ")
                    time.sleep(0.2)
        except socket.timeout:
            pass
        return output

    def execute_command(self, command: str, timeout: int = None) -> str:
        """Executa um comando e retorna a saída completa."""
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
                    # Trata paginação "-- More --"
                    if re.search(r'--\s*[Mm]ore\s*--', chunk):
                        self.shell.send(" ")
                        time.sleep(0.2)
                        continue
                    # Verifica se chegou ao prompt
                    if re.search(r'[>#]\s*$', chunk.strip()):
                        break
                else:
                    time.sleep(0.1)
                    if not self.shell.recv_ready():
                        # Aguarda mais um pouco para garantir que a saída está completa
                        time.sleep(0.3)
                        if not self.shell.recv_ready():
                            break
        except socket.timeout:
            pass

        # Remove o comando enviado da saída e o prompt final
        lines = output.split('\n')
        clean_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped and not stripped.endswith(command.strip()):
                # Remove linhas de prompt (terminam com # ou >)
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
            self.tn = telnetlib.Telnet(self.ip, self.port, timeout=settings.SSH_TIMEOUT)
            # Login
            self.tn.read_until(b"Username:", timeout=10)
            self.tn.write(self.username.encode("ascii") + b"\n")
            self.tn.read_until(b"Password:", timeout=10)
            self.tn.write(self.password.encode("ascii") + b"\n")
            # Aguarda prompt
            output = self.tn.read_until(b"#", timeout=10).decode("utf-8", errors="replace")
            if "#" not in output and ">" not in output:
                raise OLTConnectionError("Login Telnet falhou - prompt não encontrado")
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
    if protocol == "ssh":
        return OLTSSHClient(ip, port, username, password)
    elif protocol == "telnet":
        return OLTTelnetClient(ip, port, username, password)
    else:
        raise ValueError(f"Protocolo não suportado: {protocol}")


# ============================================================
# PARSERS DE SAÍDA DOS COMANDOS ZTE TITAN
# ============================================================

def parse_onu_state(output: str) -> List[Dict]:
    """
    Parseia a saída de: show gpon onu state gpon-olt_X/X/X
    Retorna lista de ONUs com seus estados.
    """
    onus = []
    lines = output.split('\n')
    for line in lines:
        line = line.strip()
        # Padrão: 1/2/2:85    enable    working    -
        match = re.match(
            r'(\d+/\d+/\d+:\d+)\s+(enable|disable)\s+(working|disable|initial|ranging|standby|unknown)\s*(\S*)',
            line
        )
        if match:
            onu_index = match.group(1)
            admin_state = match.group(2)
            oper_state = match.group(3)
            last_down = match.group(4) if match.group(4) and match.group(4) != '-' else None

            # Define cor de status
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
    """Parseia a saída de: show gpon onu detail-info gpon-onu_X/X/X:Y"""
    result = {"onu_index": onu_index}
    patterns = {
        "serial_number": r'Serial\s*[Nn]umber\s*[:\-]\s*(\S+)',
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
        match = re.search(pattern, output)
        if match:
            result[key] = match.group(1).strip()
    return result


def parse_onu_power(output: str, onu_index: str) -> Dict:
    """Parseia a saída de: show pon power attenuation gpon-onu_X/X/X:Y"""
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
    """Parseia a saída de: show pon power olt-rx gpon-olt_X/X/X"""
    results = []
    lines = output.split('\n')
    for line in lines:
        match = re.match(r'(\d+/\d+/\d+:\d+)\s+([-\d\.]+)\s*dBm', line.strip())
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
    """Parseia a saída de: show gpon onu distance gpon-onu_X/X/X:Y"""
    result = {"onu_index": onu_index}
    match = re.search(r'[Dd]istance\s*[:\-]?\s*(\d+)\s*m', output)
    if match:
        result["distance_m"] = int(match.group(1))
    return result


def parse_onu_wan(output: str, onu_index: str) -> Dict:
    """Parseia a saída de: show gpon remote-onu wan-info gpon-onu_X/X/X:Y"""
    result = {"onu_index": onu_index}
    patterns = {
        "connection_type": r'[Cc]onnection\s*[Tt]ype\s*[:\-]\s*(\S+)',
        "status": r'[Ss]tatus\s*[:\-]\s*(Connected|Connecting|Disconnected|connected|connecting|disconnected)',
        "ip_address": r'IP\s*[Aa]ddress\s*[:\-]\s*(\d+\.\d+\.\d+\.\d+)',
        "gateway": r'[Gg]ateway\s*[:\-]\s*(\d+\.\d+\.\d+\.\d+)',
        "dns": r'DNS\s*[:\-]\s*(\d+\.\d+\.\d+\.\d+)',
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, output)
        if match:
            result[key] = match.group(1).strip()
    return result


def parse_onu_voip(output: str, onu_index: str) -> Dict:
    """Parseia a saída de: show gpon remote-onu voip-status gpon-onu_X/X/X:Y"""
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
        match = re.search(pattern, output)
        if match:
            result[key] = match.group(1).strip()
    return result


def parse_onu_baseinfo(output: str) -> List[Dict]:
    """Parseia a saída de: show gpon onu baseinfo gpon-olt_X/X/X"""
    onus = []
    lines = output.split('\n')
    for line in lines:
        line = line.strip()
        # Padrão: 1/2/2:85    ZTEG12345678    F601    enable    working
        match = re.match(r'(\d+/\d+/\d+:\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s*(\S*)', line)
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
    """Parseia a saída de: show gpon onu uncfg"""
    onus = []
    lines = output.split('\n')
    for line in lines:
        line = line.strip()
        match = re.match(r'(gpon-onu_\d+/\d+/\d+:\d+)\s+(\S+)', line)
        if match:
            onus.append({
                "onu_index": match.group(1).replace("gpon-onu_", ""),
                "serial": match.group(2)
            })
    return onus


def parse_olt_ports(output: str) -> List[Dict]:
    """Parseia as portas PON disponíveis na OLT."""
    ports = []
    lines = output.split('\n')
    for line in lines:
        # Detecta linhas com portas gpon-olt
        match = re.search(r'gpon-olt_(\d+)/(\d+)/(\d+)', line)
        if match:
            slot = int(match.group(1))
            card = int(match.group(2))
            port = int(match.group(3))
            ports.append({
                "slot": slot,
                "card": card,
                "port": port,
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
    """Descobre as portas PON disponíveis na OLT."""
    client = None
    try:
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        # Tenta diferentes comandos para listar portas
        ports = []
        commands_to_try = [
            "show interface gpon-olt",
            "show running-config interface gpon-olt",
        ]

        for cmd in commands_to_try:
            output = client.execute_command(cmd)
            ports = parse_olt_ports(output)
            if ports:
                break

        # Se não encontrou, tenta descoberta por força bruta (slots 1-4, ports 1-16)
        if not ports:
            for slot in range(1, 5):
                for p in range(1, 17):
                    test_output = client.execute_command(f"show gpon onu baseinfo gpon-olt_{slot}/1/{p}")
                    if "gpon-onu" in test_output.lower() or re.search(r'\d+/\d+/\d+:\d+', test_output):
                        ports.append({
                            "slot": slot,
                            "card": 1,
                            "port": p,
                            "port_type": "gpon",
                            "description": f"GPON Slot {slot} Port {p}"
                        })

        return ports
    except Exception as e:
        raise OLTConnectionError(f"Falha na descoberta: {str(e)}")
    finally:
        if client:
            client.disconnect()


def get_pon_onu_status(ip: str, port: int, username: str, password: str, protocol: str,
                       slot: int, pon_port: int) -> List[Dict]:
    """Obtém o status de todas as ONUs de uma porta PON."""
    client = None
    try:
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()
        cmd = f"show gpon onu state gpon-olt_{slot}/1/{pon_port}"
        output = client.execute_command(cmd)
        return parse_onu_state(output)
    finally:
        if client:
            client.disconnect()


def get_onu_full_info(ip: str, port: int, username: str, password: str, protocol: str,
                      slot: int, pon_port: int, onu_id: int) -> Dict:
    """Obtém informações completas de uma ONU específica."""
    client = None
    try:
        client = get_olt_client(ip, port, username, password, protocol)
        client.connect()

        onu_ref = f"gpon-onu_{slot}/1/{pon_port}:{onu_id}"
        olt_ref = f"gpon-olt_{slot}/1/{pon_port}"
        onu_index = f"{slot}/1/{pon_port}:{onu_id}"

        result = {}

        # Estado
        out = client.execute_command(f"show gpon onu state {onu_ref}")
        states = parse_onu_state(out)
        if states:
            result["status"] = states[0]
        else:
            # Tenta pelo índice da OLT
            out2 = client.execute_command(f"show gpon onu state {olt_ref}")
            all_states = parse_onu_state(out2)
            for s in all_states:
                if s["onu_index"] == onu_index:
                    result["status"] = s
                    break

        # Detalhes
        out = client.execute_command(f"show gpon onu detail-info {onu_ref}")
        result["detail"] = parse_onu_detail(out, onu_index)

        # Potência
        out = client.execute_command(f"show pon power attenuation {onu_ref}")
        result["power"] = parse_onu_power(out, onu_index)

        # Distância
        out = client.execute_command(f"show gpon onu distance {onu_ref}")
        result["distance"] = parse_onu_distance(out, onu_index)

        # WAN
        out = client.execute_command(f"show gpon remote-onu wan-info {onu_ref}")
        result["wan"] = parse_onu_wan(out, onu_index)

        # VoIP
        out = client.execute_command(f"show gpon remote-onu voip-status {onu_ref}")
        result["voip"] = parse_onu_voip(out, onu_index)

        # Temperatura
        out = client.execute_command(f"show gpon onu temperature {onu_ref}")
        result["temperature"] = parse_onu_temperature(out, onu_index)

        # Firmware
        out = client.execute_command(f"show gpon onu firmware-version {onu_ref}")
        result["firmware"] = parse_onu_firmware(out, onu_index)

        return result
    finally:
        if client:
            client.disconnect()
