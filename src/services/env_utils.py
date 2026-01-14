import os
from typing import Optional


def get_env(name: str, default: Optional[str] = None) -> Optional[str]:
    """Read an environment variable in Azure App Service-friendly way.

    In App Service (especially custom containers), settings are sometimes injected as
    APPSETTING_<NAME>. This helper checks both.
    """
    value = os.getenv(name)
    if value is not None and value != "":
        return value

    prefixed = os.getenv(f"APPSETTING_{name}")
    if prefixed is not None and prefixed != "":
        return prefixed

    return default


def get_env_with_source(name: str, default: Optional[str] = None) -> tuple[Optional[str], str]:
    """Return (value, source) where source indicates which variable was used."""
    value = os.getenv(name)
    if value is not None and value != "":
        return value, name

    prefixed_name = f"APPSETTING_{name}"
    prefixed = os.getenv(prefixed_name)
    if prefixed is not None and prefixed != "":
        return prefixed, prefixed_name

    return default, "default"


def is_running_in_azure() -> bool:
    """Best-effort detection for Azure App Service."""
    return bool(os.getenv("WEBSITE_SITE_NAME") or os.getenv("APPSETTING_WEBSITE_SITE_NAME"))
