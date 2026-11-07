"""Сбор информации о системе через WMI.

Вызывать только из потока, где выполнен pythoncom.CoInitialize().
Каждый раздел собирается независимо: ошибка в одном не ломает остальные.
"""
from __future__ import annotations

import logging
import winreg
from collections.abc import Callable
from datetime import datetime
from typing import Any

import pywintypes
import win32com.client

from .gpu_vendors import detect_vendor
from .models import KV_COLUMNS, GpuInfo, Section, SystemReport
from .utils import fmt_bytes, fmt_cim_date, to_str

logger = logging.getLogger(__name__)

ProgressCb = Callable[[str], None]

_GPU_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


class WmiClient:
    """Тонкая обёртка над WMI-сервисами (cimv2 и root\\wmi)."""

    def __init__(self) -> None:
        self._cimv2 = win32com.client.GetObject(r"winmgmts:\\.\root\cimv2")
        self._wmi: Any | None = None
        self._storage: Any | None = None

    def query(self, wql: str, namespace: str = "cimv2") -> list[Any]:
        if namespace == "cimv2":
            service = self._cimv2
        elif namespace == "storage":
            service = self._storage_service()
        else:
            service = self._root_wmi()
        return list(service.ExecQuery(wql))

    def _storage_service(self) -> Any:
        if self._storage is None:
            self._storage = win32com.client.GetObject(r"winmgmts:\\.\root\Microsoft\Windows\Storage")
        return self._storage

    def _root_wmi(self) -> Any:
        if self._wmi is None:
            self._wmi = win32com.client.GetObject(r"winmgmts:\\.\root\wmi")
        return self._wmi


def prop(obj: Any, name: str) -> Any:
    """Чтение свойства WMI-объекта; отсутствующее свойство -> None."""
    try:
        return getattr(obj, name)
    except (AttributeError, pywintypes.com_error):
        return None


def _kv(pairs: list[tuple[str, Any]]) -> list[list[str]]:
    return [[k, to_str(v)] for k, v in pairs if to_str(v)]


_PLACEHOLDER_VALUES = {"invalid", "to be filled by o.e.m.", "default string", "system product name", "none", "n/a"}
_BUS_TYPES = {7: "USB", 8: "RAID", 10: "SAS", 11: "SATA", 17: "NVMe"}
_MEDIA_TYPES = {3: "HDD", 4: "SSD"}
_WMI_SPEED_UNKNOWN = 2**62  # Windows отдаёт ~2^63-1 для неактивных адаптеров


def _clean(value: Any) -> str:
    """Пустая строка вместо OEM-заглушек вида 'INVALID' / 'To be filled by O.E.M.'."""
    text = to_str(value)
    return "" if text.lower() in _PLACEHOLDER_VALUES else text


# ---------- Разделы ----------

def _product_name(wmi: WmiClient) -> str:
    """Запасное имя модели из Win32_ComputerSystemProduct (Version / Name)."""
    for p in wmi.query("SELECT * FROM Win32_ComputerSystemProduct"):
        for field in ("Version", "Name"):
            value = _clean(prop(p, field))
            if value:
                return value
    return ""


def _overview(wmi: WmiClient) -> Section:
    rows: list[list[str]] = []
    for os_ in wmi.query("SELECT * FROM Win32_OperatingSystem"):
        rows += _kv([
            ("ОС", prop(os_, "Caption")),
            ("Версия", prop(os_, "Version")),
            ("Сборка", prop(os_, "BuildNumber")),
            ("Архитектура", prop(os_, "OSArchitecture")),
            ("Установлена", fmt_cim_date(prop(os_, "InstallDate"), with_time=True)),
            ("Последняя загрузка", fmt_cim_date(prop(os_, "LastBootUpTime"), with_time=True)),
        ])
    for cs in wmi.query("SELECT * FROM Win32_ComputerSystem"):
        rows += _kv([
            ("Имя компьютера", prop(cs, "Name")),
            ("Производитель", prop(cs, "Manufacturer")),
            ("Модель", _clean(prop(cs, "Model")) or _product_name(wmi)),
            ("ОЗУ всего", fmt_bytes(prop(cs, "TotalPhysicalMemory"))),
        ])
    for bb in wmi.query("SELECT * FROM Win32_BaseBoard"):
        product = _clean(prop(bb, "Product"))
        if product:
            rows += _kv([("Материнская плата", f"{to_str(prop(bb, 'Manufacturer'))} {product}")])
    for bios in wmi.query("SELECT * FROM Win32_BIOS"):
        rows += _kv([
            ("BIOS", f"{to_str(prop(bios, 'Manufacturer'))} {to_str(prop(bios, 'SMBIOSBIOSVersion'))}"),
            ("Дата BIOS", fmt_cim_date(prop(bios, "ReleaseDate"))),
        ])
    return Section("overview", "Обзор", KV_COLUMNS, rows)


def _cpu(wmi: WmiClient) -> Section:
    rows: list[list[str]] = []
    for cpu in wmi.query("SELECT * FROM Win32_Processor"):
        rows += _kv([
            ("Модель", prop(cpu, "Name")),
            ("Производитель", prop(cpu, "Manufacturer")),
            ("Ядра", prop(cpu, "NumberOfCores")),
            ("Потоки", prop(cpu, "NumberOfLogicalProcessors")),
            ("Базовая частота, МГц", prop(cpu, "MaxClockSpeed")),
            ("Кэш L2", fmt_bytes((prop(cpu, "L2CacheSize") or 0) * 1024)),
            ("Кэш L3", fmt_bytes((prop(cpu, "L3CacheSize") or 0) * 1024)),
            ("Сокет", prop(cpu, "SocketDesignation")),
        ])
    return Section("cpu", "Процессор", KV_COLUMNS, rows)


def _memory(wmi: WmiClient) -> Section:
    cols = ["Слот", "Объём", "Частота, МГц", "Производитель", "Партномер"]
    rows = [
        [
            " ".join(x for x in (to_str(prop(m, "BankLabel")), to_str(prop(m, "DeviceLocator"))) if x),
            fmt_bytes(prop(m, "Capacity")),
            to_str(prop(m, "ConfiguredClockSpeed") or prop(m, "Speed")),
            to_str(prop(m, "Manufacturer")),
            to_str(prop(m, "PartNumber")),
        ]
        for m in wmi.query("SELECT * FROM Win32_PhysicalMemory")
    ]
    return Section("memory", "Память", cols, rows)


def _physical_disk_info(wmi: WmiClient) -> dict[str, tuple[str, str]]:
    """DeviceId -> (тип шины, тип носителя) из Storage Management; пусто, если недоступно."""
    info: dict[str, tuple[str, str]] = {}
    try:
        for pd in wmi.query("SELECT DeviceId, BusType, MediaType FROM MSFT_PhysicalDisk", namespace="storage"):
            info[to_str(prop(pd, "DeviceId"))] = (
                _BUS_TYPES.get(int(prop(pd, "BusType") or 0), ""),
                _MEDIA_TYPES.get(int(prop(pd, "MediaType") or 0), ""),
            )
    except pywintypes.com_error:
        logger.warning("Storage Management WMI недоступен, тип шины берём из Win32_DiskDrive")
    return info


def _disks(wmi: WmiClient) -> Section:
    cols = ["Устройство", "Модель / ФС", "Интерфейс", "Размер", "Свободно"]
    rows: list[list[str]] = []
    phys = _physical_disk_info(wmi)
    for d in wmi.query("SELECT * FROM Win32_DiskDrive"):
        bus, media = phys.get(to_str(prop(d, "Index")), ("", ""))
        interface = " ".join(x for x in (bus, media) if x) or to_str(prop(d, "InterfaceType"))
        rows.append([
            to_str(prop(d, "DeviceID")),
            to_str(prop(d, "Model")),
            interface,
            fmt_bytes(prop(d, "Size")),
            "",
        ])
    for ld in wmi.query("SELECT * FROM Win32_LogicalDisk WHERE DriveType=3"):
        rows.append([
            to_str(prop(ld, "DeviceID")),
            to_str(prop(ld, "FileSystem")),
            "Логический",
            fmt_bytes(prop(ld, "Size")),
            fmt_bytes(prop(ld, "FreeSpace")),
        ])
    return Section("disks", "Диски", cols, rows)


def _gpu_vram_from_registry() -> dict[str, int]:
    """Точный объём VRAM (WMI AdapterRAM ограничен 4 ГБ из-за uint32)."""
    result: dict[str, int] = {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _GPU_CLASS_KEY)
    except OSError:
        logger.warning("Нет доступа к ветке реестра видеоадаптеров")
        return result
    with root:
        index = 0
        while True:
            try:
                sub = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as key:
                    desc, _ = winreg.QueryValueEx(key, "DriverDesc")
                    raw, _ = winreg.QueryValueEx(key, "HardwareInformation.qwMemorySize")
            except OSError:
                continue  # у подключа нет нужных значений — это нормально
            size = int.from_bytes(raw, "little") if isinstance(raw, bytes) else int(raw)
            result[str(desc)] = size
    return result


def _video(wmi: WmiClient, report: SystemReport) -> Section:
    cols = ["Видеокарта", "Видеопамять", "Драйвер", "Дата драйвера", "Разрешение", "Производитель"]
    vram = _gpu_vram_from_registry()
    rows: list[list[str]] = []
    for vc in wmi.query("SELECT * FROM Win32_VideoController"):
        name = to_str(prop(vc, "Name"))
        mem = vram.get(name) or prop(vc, "AdapterRAM")
        w, h, hz = prop(vc, "CurrentHorizontalResolution"), prop(vc, "CurrentVerticalResolution"), prop(vc, "CurrentRefreshRate")
        resolution = f"{w}x{h} @ {hz} Гц" if w and h else ""
        vendor, url = detect_vendor(to_str(prop(vc, "PNPDeviceID")), name)
        gpu = GpuInfo(
            name=name,
            driver_version=to_str(prop(vc, "DriverVersion")),
            driver_date=fmt_cim_date(prop(vc, "DriverDate")),
            vendor=vendor,
            download_url=url,
        )
        report.gpus.append(gpu)
        rows.append([name, fmt_bytes(mem), gpu.driver_version, gpu.driver_date, resolution, vendor or ""])
    return Section("video", "Видео", cols, rows)


def _decode_wmi_string(arr: Any) -> str:
    if not arr:
        return ""
    return "".join(chr(c) for c in arr if c).strip()


def _monitors(wmi: WmiClient) -> Section:
    cols = ["Монитор", "Производитель", "Серийный номер", "Год выпуска"]
    rows: list[list[str]] = []
    try:
        for m in wmi.query("SELECT * FROM WmiMonitorID", namespace="wmi"):
            rows.append([
                _decode_wmi_string(prop(m, "UserFriendlyName"))
                or f"Встроенный / безымянный экран ({_decode_wmi_string(prop(m, 'ManufacturerName')) or '?'})",
                _decode_wmi_string(prop(m, "ManufacturerName")),
                _decode_wmi_string(prop(m, "SerialNumberID")).strip("0") and _decode_wmi_string(prop(m, "SerialNumberID")),
                to_str(prop(m, "YearOfManufacture")),
            ])
    except pywintypes.com_error:
        logger.warning("root\\wmi недоступен, используем Win32_DesktopMonitor")
        for m in wmi.query("SELECT * FROM Win32_DesktopMonitor"):
            rows.append([to_str(prop(m, "Name")), to_str(prop(m, "MonitorManufacturer")), "", ""])
    return Section("monitors", "Мониторы", cols, rows)


def _network(wmi: WmiClient) -> Section:
    cols = ["Адаптер", "MAC", "Скорость", "IP-адреса", "Статус"]
    configs = {
        prop(c, "Index"): c
        for c in wmi.query("SELECT Index, IPAddress FROM Win32_NetworkAdapterConfiguration")
    }
    rows: list[list[str]] = []
    for a in wmi.query("SELECT * FROM Win32_NetworkAdapter WHERE PhysicalAdapter=True"):
        cfg = configs.get(prop(a, "Index"))
        speed = prop(a, "Speed")
        rows.append([
            to_str(prop(a, "Name")),
            to_str(prop(a, "MACAddress")),
            f"{int(speed) // 1_000_000} Мбит/с" if speed and int(speed) < _WMI_SPEED_UNKNOWN else "",
            to_str(prop(cfg, "IPAddress")) if cfg is not None else "",
            "Подключён" if prop(a, "NetConnectionStatus") == 2 else "Не подключён",
        ])
    return Section("network", "Сеть", cols, rows)


def _audio(wmi: WmiClient) -> Section:
    cols = ["Устройство", "Производитель", "Статус"]
    rows = [
        [to_str(prop(s, "Name")), to_str(prop(s, "Manufacturer")), to_str(prop(s, "Status"))]
        for s in wmi.query("SELECT * FROM Win32_SoundDevice")
    ]
    return Section("audio", "Звук", cols, rows)


def _drivers(wmi: WmiClient) -> Section:
    cols = ["Устройство", "Класс", "Поставщик", "Версия", "Дата", "INF", "Подписан"]
    rows: list[list[str]] = []
    wql = (
        "SELECT DeviceName, DeviceClass, DriverProviderName, DriverVersion, DriverDate, InfName, IsSigned "
        "FROM Win32_PnPSignedDriver"
    )
    for d in wmi.query(wql):
        name = to_str(prop(d, "DeviceName"))
        if not name:
            continue
        rows.append([
            name,
            to_str(prop(d, "DeviceClass")),
            to_str(prop(d, "DriverProviderName")),
            to_str(prop(d, "DriverVersion")),
            fmt_cim_date(prop(d, "DriverDate")),
            to_str(prop(d, "InfName")),
            to_str(prop(d, "IsSigned")),
        ])
    rows.sort(key=lambda r: (r[1].lower(), r[0].lower()))
    return Section("drivers", "Драйверы", cols, rows)


# ---------- Точка входа ----------

def scan_system(progress: ProgressCb | None = None) -> SystemReport:
    report = SystemReport(scanned_at=datetime.now())
    wmi = WmiClient()

    steps: list[tuple[str, str, Callable[[], Section]]] = [
        ("overview", "Обзор", lambda: _overview(wmi)),
        ("cpu", "Процессор", lambda: _cpu(wmi)),
        ("memory", "Память", lambda: _memory(wmi)),
        ("disks", "Диски", lambda: _disks(wmi)),
        ("video", "Видео", lambda: _video(wmi, report)),
        ("monitors", "Мониторы", lambda: _monitors(wmi)),
        ("network", "Сеть", lambda: _network(wmi)),
        ("audio", "Звук", lambda: _audio(wmi)),
        ("drivers", "Драйверы", lambda: _drivers(wmi)),
    ]
    for key, title, collect in steps:
        if progress:
            progress(f"Сканирование: {title}…")
        try:
            report.sections.append(collect())
        except (pywintypes.com_error, OSError, ValueError, TypeError) as exc:
            logger.exception("Ошибка сбора раздела %s", key)
            report.sections.append(Section(key, title, KV_COLUMNS, [], error=str(exc)))
    return report

