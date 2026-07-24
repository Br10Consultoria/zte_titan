"""Compatibilidade para imports antigos de app.olt_driver.

Os drivers reais ficam separados por vendor em app.olt_drivers.
"""
from .olt_drivers import (  # noqa: F401
    DEFAULT_DRIVER,
    DRIVERS,
    OLTDriver,
    OLT_MODELS,
    Parks30004000Driver,
    ZTEC300Driver,
    ZTEC600Driver,
    _parse_onu_traffic_common,
    detect_model,
    get_driver,
)
