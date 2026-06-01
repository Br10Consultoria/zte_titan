from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from typing import List

from ..database import get_db
from ..models import User, AuditLog
from ..schemas import (
    LoginRequest, TokenResponse, TwoFAVerify, TwoFASetupResponse,
    UserCreate, UserUpdate, UserResponse, ChangePasswordRequest
)
from ..auth import (
    verify_password, get_password_hash, create_access_token,
    generate_totp_secret, get_totp_uri, generate_qr_code_base64,
    verify_totp, get_current_user, get_current_admin, get_partial_user
)
from ..config import settings

router = APIRouter(prefix="/auth", tags=["Autenticação"])


def log_action(db: Session, user_id: int, username: str, action: str, resource: str = None,
               ip_address: str = None, details: str = None):
    log = AuditLog(
        user_id=user_id,
        username=username,
        action=action,
        resource=resource,
        ip_address=ip_address,
        details=details
    )
    db.add(log)
    db.commit()


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, req: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == request.username).first()

    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário ou senha incorretos"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Conta desativada. Contate o administrador."
        )

    # Se o 2FA está habilitado, emite um token parcial (sem 2fa_verified)
    if user.is_2fa_enabled:
        token_data = {"sub": user.username, "2fa_verified": False}
        token = create_access_token(token_data, expires_delta=timedelta(minutes=10))
        log_action(db, user.id, user.username, "LOGIN_PARTIAL", ip_address=req.client.host)
        return TokenResponse(access_token=token, requires_2fa=True)

    # Login completo sem 2FA
    token_data = {"sub": user.username, "role": user.role, "2fa_verified": True}
    token = create_access_token(token_data)

    user.last_login = datetime.now()
    db.commit()

    log_action(db, user.id, user.username, "LOGIN_SUCCESS", ip_address=req.client.host)

    return TokenResponse(
        access_token=token,
        requires_2fa=False,
        user=UserResponse.model_validate(user)
    )


@router.post("/verify-2fa", response_model=TokenResponse)
def verify_2fa(
    body: TwoFAVerify,
    req: Request,
    current_user: User = Depends(get_partial_user),
    db: Session = Depends(get_db)
):
    if not current_user.is_2fa_enabled or not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="2FA não está habilitado para este usuário")

    if not verify_totp(current_user.totp_secret, body.totp_code):
        raise HTTPException(status_code=401, detail="Código 2FA inválido ou expirado")

    # Emite token completo com 2FA verificado
    token_data = {"sub": current_user.username, "role": current_user.role, "2fa_verified": True}
    token = create_access_token(token_data)

    current_user.last_login = datetime.now()
    db.commit()

    log_action(db, current_user.id, current_user.username, "2FA_VERIFIED", ip_address=req.client.host)

    return TokenResponse(
        access_token=token,
        requires_2fa=False,
        user=UserResponse.model_validate(current_user)
    )


@router.get("/2fa/setup", response_model=TwoFASetupResponse)
def setup_2fa(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Gera um novo segredo TOTP e QR Code para o usuário configurar o 2FA."""
    secret = generate_totp_secret()
    provisioning_uri = get_totp_uri(secret, current_user.username)
    qr_code_base64 = generate_qr_code_base64(provisioning_uri)

    # Salva o segredo temporariamente (ainda não ativado)
    current_user.totp_secret = secret
    db.commit()

    return TwoFASetupResponse(
        secret=secret,
        qr_code_url=qr_code_base64,
        provisioning_uri=provisioning_uri
    )


@router.post("/2fa/enable")
def enable_2fa(
    body: TwoFAVerify,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Confirma e ativa o 2FA após o usuário escanear o QR Code."""
    if not current_user.totp_secret:
        raise HTTPException(status_code=400, detail="Configure o 2FA primeiro")

    if not verify_totp(current_user.totp_secret, body.totp_code):
        raise HTTPException(status_code=401, detail="Código 2FA inválido. Verifique o aplicativo.")

    current_user.is_2fa_enabled = True
    db.commit()

    return {"message": "Autenticação de dois fatores ativada com sucesso!"}


@router.post("/2fa/disable")
def disable_2fa(
    body: TwoFAVerify,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Desativa o 2FA após confirmar com o código atual."""
    if not current_user.is_2fa_enabled:
        raise HTTPException(status_code=400, detail="2FA não está habilitado")

    if not verify_totp(current_user.totp_secret, body.totp_code):
        raise HTTPException(status_code=401, detail="Código 2FA inválido")

    current_user.is_2fa_enabled = False
    current_user.totp_secret = None
    db.commit()

    return {"message": "Autenticação de dois fatores desativada."}


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/change-password")
def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not verify_password(body.current_password, current_user.password_hash):
        raise HTTPException(status_code=401, detail="Senha atual incorreta")

    current_user.password_hash = get_password_hash(body.new_password)
    db.commit()

    return {"message": "Senha alterada com sucesso!"}


# ============================================================
# GERENCIAMENTO DE USUÁRIOS (ADMIN)
# ============================================================

@router.get("/users", response_model=List[UserResponse])
def list_users(
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    return db.query(User).all()


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    body: UserCreate,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    existing = db.query(User).filter(User.username == body.username).first()
    if existing:
        raise HTTPException(status_code=400, detail="Nome de usuário já existe")

    user = User(
        username=body.username,
        email=body.email,
        full_name=body.full_name,
        password_hash=get_password_hash(body.password),
        role=body.role or "viewer",
        is_active=True,
        is_2fa_enabled=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    body: UserUpdate,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    if body.email is not None:
        user.email = body.email
    if body.full_name is not None:
        user.full_name = body.full_name
    if body.role is not None:
        user.role = body.role
    if body.is_active is not None:
        user.is_active = body.is_active

    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="Você não pode excluir sua própria conta")

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    db.delete(user)
    db.commit()
    return {"message": "Usuário excluído com sucesso"}


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    body: dict,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")

    new_password = body.get("new_password")
    if not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Senha deve ter no mínimo 6 caracteres")

    user.password_hash = get_password_hash(new_password)
    db.commit()
    return {"message": "Senha redefinida com sucesso"}
