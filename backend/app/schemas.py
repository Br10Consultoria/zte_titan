from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


# ============================================================
# USER SCHEMAS
# ============================================================

class UserCreate(BaseModel):
    username: str
    password: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: str = "viewer"
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: str
    is_active: bool
    is_2fa_enabled: bool
    created_at: datetime
    last_login: Optional[datetime] = None

    class Config:
        from_attributes = True


class LoginRequest(BaseModel):
    username: str
    password: str
    totp_code: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    requires_2fa: bool = False
    user: Optional[UserResponse] = None


class TOTPSetupResponse(BaseModel):
    secret: str
    qr_code_url: str
    provisioning_uri: str


class TwoFAVerify(BaseModel):
    totp_code: str


class TwoFASetupResponse(BaseModel):
    secret: str
    qr_code_url: str
    provisioning_uri: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


# ============================================================
# OLT SCHEMAS
# ============================================================

class OLTCreate(BaseModel):
    name: str
    ip: str
    port: int = 23
    username: str
    password: str
    protocol: str = "telnet"
    snmp_community: Optional[str] = None
    snmp_version: Optional[str] = "2c"
    olt_model: Optional[str] = "zte_c600"  # chave do driver


class OLTUpdate(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: Optional[str] = None
    snmp_community: Optional[str] = None
    snmp_version: Optional[str] = None
    olt_model: Optional[str] = None


class OLTResponse(BaseModel):
    id: int
    name: str
    ip: str
    port: int
    username: str
    protocol: str
    snmp_community: Optional[str] = None
    snmp_version: Optional[str] = None
    status: str
    model: Optional[str] = None
    olt_model: Optional[str] = None
    firmware: Optional[str] = None
    last_check: Optional[datetime] = None
    created_at: datetime

    class Config:
        from_attributes = True


class OLTPortResponse(BaseModel):
    id: int
    olt_id: int
    slot: int
    card: int = 1     # subslot/card: gpon-olt_SLOT/CARD/PON
    pon: int          # porta PON
    port_type: str
    description: Optional[str] = None
    status: str
    onu_count: int
    onu_max: int = 128   # capacidade máxima da PON (padrão 128)
    discovered_at: datetime

    class Config:
        from_attributes = True


# ============================================================
# ONU SCHEMAS
# ============================================================

class ONUStatus(BaseModel):
    onu_index: str
    admin_state: str
    oper_state: str
    last_down_cause: Optional[str] = None
    status_color: str
    olt_rx_power: Optional[float] = None
    olt_rx_status: Optional[str] = None


class PONStatusResponse(BaseModel):
    olt_id: int
    slot: int
    pon: int
    olt_interface: str
    onus: List[ONUStatus]
    total: int
    online: int
    offline: int
    cached: bool
    last_updated: str


class ONUFullInfo(BaseModel):
    onu_index: str
    olt_id: int
    onu_interface: str
    status: Optional[Dict] = None
    detail: Optional[Dict] = None
    power: Optional[Dict] = None
    distance: Optional[Dict] = None
    wan: Optional[Dict] = None
    voip: Optional[Dict] = None
    temperature: Optional[Dict] = None
    firmware: Optional[Dict] = None
    cached: bool
    last_updated: str


# ============================================================
# BACKUP SCHEMAS
# ============================================================

class BackupSettingsUpdate(BaseModel):
    server_ip: Optional[str] = None
    ftp_bind_host: str = "0.0.0.0"
    ftp_port: int = 21
    ftp_passive_ports: str = "30000-30009"
    ftp_user: str = "ztebackup"
    ftp_password: Optional[str] = None
    source_path: str = "/datadisk0/DATA0/startrun.dat"
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    telegram_enabled: bool = True
    keep_local: bool = True


class BackupSettingsResponse(BackupSettingsUpdate):
    id: int
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BackupRunRequest(BaseModel):
    olt_id: int
    send_telegram: bool = True


class BackupJobResponse(BaseModel):
    id: int
    olt_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime] = None
    filename: Optional[str] = None
    file_size: Optional[int] = None
    sha256: Optional[str] = None
    telegram_sent: bool = False
    message: Optional[str] = None
    command_output: Optional[str] = None

    class Config:
        from_attributes = True
