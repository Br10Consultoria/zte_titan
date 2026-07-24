"""Registro de drivers multivendor."""
import re
from typing import Dict, Optional

from .base import OLTDriver, OLT_MODELS
from .parks import Parks30004000Driver
from .zte import ZTEC300Driver, ZTEC600Driver

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
