from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import os

from .config import settings
from .database import init_db, SessionLocal
from .auth import create_default_admin
from .routes import auth, olts, onus

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Sistema de Gerenciamento de ONUs ZTE Titan - API REST",
    docs_url="/api/docs",
    redoc_url="/api/redoc"
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas da API
app.include_router(auth.router, prefix="/api")
app.include_router(olts.router, prefix="/api")
app.include_router(onus.router, prefix="/api")


@app.get("/api/health")
def health_check():
    from .redis_client import cache
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "redis": cache.is_available()
    }


# Servir arquivos estáticos do frontend
frontend_path = os.path.join(os.path.dirname(__file__), "..", "..", "frontend")
if os.path.exists(frontend_path):
    app.mount("/static", StaticFiles(directory=os.path.join(frontend_path, "static")), name="static")

    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        index_file = os.path.join(frontend_path, "index.html")
        if os.path.exists(index_file):
            return FileResponse(index_file)
        return {"error": "Frontend não encontrado"}


@app.on_event("startup")
def startup_event():
    print(f"🚀 Iniciando {settings.APP_NAME} v{settings.APP_VERSION}")
    init_db()
    _migrate_db()
    db = SessionLocal()
    try:
        create_default_admin(db)
    finally:
        db.close()
    print("✅ Sistema iniciado com sucesso!")
    print(f"📖 Documentação API: http://localhost:8000/api/docs")


def _migrate_db():
    """
    Aplica migrações incrementais no banco SQLite sem perder dados.
    Renomeia card/port para pon conforme sintaxe ZTE Titan (gpon-olt_SLOT/PON).
    """
    from .database import engine
    import sqlalchemy as sa

    with engine.connect() as conn:
        # Verifica colunas existentes na tabela olt_ports
        try:
            result = conn.execute(sa.text("PRAGMA table_info(olt_ports)"))
            cols = {row[1] for row in result.fetchall()}

            # Se ainda tem a coluna 'port' antiga (sem 'pon'), migra
            if "port" in cols and "pon" not in cols:
                conn.execute(sa.text("ALTER TABLE olt_ports ADD COLUMN pon INTEGER NOT NULL DEFAULT 1"))
                # Copia o valor de 'port' para 'pon'
                conn.execute(sa.text("UPDATE olt_ports SET pon = port"))
                conn.commit()
                print("✅ Migração: coluna 'pon' criada a partir de 'port'")

            # Se tem 'card' mas não 'pon', usa card como pon
            elif "card" in cols and "pon" not in cols:
                conn.execute(sa.text("ALTER TABLE olt_ports ADD COLUMN pon INTEGER NOT NULL DEFAULT 1"))
                conn.execute(sa.text("UPDATE olt_ports SET pon = card"))
                conn.commit()
                print("✅ Migração: coluna 'pon' criada a partir de 'card'")

            # Garante que pon existe
            elif "pon" not in cols:
                conn.execute(sa.text("ALTER TABLE olt_ports ADD COLUMN pon INTEGER NOT NULL DEFAULT 1"))
                conn.commit()
                print("✅ Migração: coluna 'pon' adicionada")

        except Exception as e:
            print(f"⚠️  Migração: {e}")
