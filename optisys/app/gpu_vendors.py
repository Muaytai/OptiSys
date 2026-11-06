"""Определение производителя видеокарты по PNP ID и ссылки на официальные драйверы."""
from __future__ import annotations

import re

# PCI Vendor ID -> (название, страница загрузки драйверов)
_VENDORS: dict[str, tuple[str, str]] = {
    "10DE": ("NVIDIA", "https://www.nvidia.com/en-us/drivers/"),
    "1002": ("AMD", "https://www.amd.com/en/support/download/drivers.html"),
    "1022": ("AMD", "https://www.amd.com/en/support/download/drivers.html"),
    "8086": ("Intel", "https://www.intel.com/content/www/us/en/support/detect.html"),
}

_VEN_RE = re.compile(r"VEN_([0-9A-F]{4})", re.IGNORECASE)


def detect_vendor(pnp_device_id: str, name: str = "") -> tuple[str | None, str | None]:
    """Возвращает (vendor, url). Сначала по VEN_xxxx, затем по названию."""
    match = _VEN_RE.search(pnp_device_id or "")
    if match:
        found = _VENDORS.get(match.group(1).upper())
        if found:
            return found
    lowered = (name or "").lower()
    for vendor, url in _VENDORS.values():
        if vendor.lower() in lowered or (vendor == "AMD" and "radeon" in lowered):
            return vendor, url
    if "geforce" in lowered or "quadro" in lowered:
        return _VENDORS["10DE"]
    return None, None

