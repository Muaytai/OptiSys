# -*- mode: python ; coding: utf-8 -*-
# Сборка: pyinstaller optisys.spec --noconfirm --clean

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["win32timezone"],  # нужен pywin32 для конвертации COM-дат
    hookspath=[],
    runtime_hooks=[],
    # Неиспользуемые модули Qt — уменьшают размер exe
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
              "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="optisys",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    uac_admin=True,  # запрос прав администратора при запуске (нужно для Windows Update)
)

