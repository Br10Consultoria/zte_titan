import os
from dotenv import load_dotenv

load_dotenv()

class Settings:
    APP_NAME: str = "ZTE Titan Manager"
    APP_VERSION: str = "1.0.0"
    SECRET_KEY: str = os.getenv("SECRET_KEY", "zte-titan-super-secret-key-change-in-production-2024")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))  # 8 horas
    
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./zte_titan.db")
    
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))
    REDIS_DB: int = int(os.getenv("REDIS_DB", "0"))
    REDIS_PASSWORD: str = os.getenv("REDIS_PASSWORD", "")
    
    CACHE_TTL: int = int(os.getenv("CACHE_TTL", "86400"))  # 24 horas em segundos
    
    SSH_TIMEOUT: int = int(os.getenv("SSH_TIMEOUT", "30"))
    SSH_COMMAND_TIMEOUT: int = int(os.getenv("SSH_COMMAND_TIMEOUT", "60"))

settings = Settings()
