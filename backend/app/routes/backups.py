from pathlib import Path
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import get_current_admin, get_current_user
from ..backup_service import (
    ensure_ftp_server,
    ftp_status,
    get_or_create_settings,
    run_backup_job,
    test_telegram,
)
from ..database import get_db
from ..models import BackupJob, OLT, User
from ..schemas import (
    BackupJobResponse,
    BackupRunRequest,
    BackupSettingsResponse,
    BackupSettingsUpdate,
)

router = APIRouter(prefix="/backups", tags=["Backups"])


@router.get("/settings", response_model=BackupSettingsResponse)
def get_backup_settings(
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return get_or_create_settings(db)


@router.put("/settings", response_model=BackupSettingsResponse)
def update_backup_settings(
    body: BackupSettingsUpdate,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    settings = get_or_create_settings(db)
    for field, value in body.model_dump().items():
        setattr(settings, field, value)
    db.commit()
    db.refresh(settings)
    try:
        ensure_ftp_server(settings)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Configurações salvas, mas FTP não iniciou: {exc}")
    return settings


@router.post("/test-telegram")
def test_backup_telegram(
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    try:
        return test_telegram(db)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/ftp-status")
def get_backup_ftp_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ftp_status(db)


@router.post("/run", response_model=BackupJobResponse)
def run_backup(
    body: BackupRunRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    olt = db.query(OLT).filter(OLT.id == body.olt_id).first()
    if not olt:
        raise HTTPException(status_code=404, detail="OLT não encontrada")

    job = BackupJob(olt_id=body.olt_id, status="running")
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(run_backup_job, job.id, body.send_telegram)
    return job


@router.get("/jobs", response_model=List[BackupJobResponse])
def list_backup_jobs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(BackupJob).order_by(BackupJob.id.desc()).limit(50).all()


@router.get("/jobs/{job_id}", response_model=BackupJobResponse)
def get_backup_job(
    job_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = db.query(BackupJob).filter(BackupJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Backup não encontrado")
    return job


@router.get("/jobs/{job_id}/download")
def download_backup_job(
    job_id: int,
    current_user: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    job = db.query(BackupJob).filter(BackupJob.id == job_id).first()
    if not job or not job.file_path:
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    path = Path(job.file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado no disco")
    return FileResponse(path, filename=job.filename or path.name)
