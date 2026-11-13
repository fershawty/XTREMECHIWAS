# -*- mode: python ; coding: utf-8 -*-
"""
futures_scope.spec
==================
Receta de PyInstaller para construir FuturesScope.exe (carpeta autocontenida).

    pip install -r requirements.txt pyinstaller==6.22.3
    pyinstaller futures_scope.spec --noconfirm

Resultado:
* Windows: dist/FuturesScope/FuturesScope.exe (+ carpeta _internal, cópiala completa).
  Tu .env y tus datos se crean junto al .exe en el primer arranque.
* macOS:   dist/FuturesScope.app (Sonoma 14 o superior).
  Tu .env y tus datos se crean en ~/Library/Application Support/FuturesScope.
"""
import sys

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.hooks.tcl_tk import tcltk_info

# La ventana de arranque usa tkinter. Si Tcl/Tk no funciona en el Python que compila,
# PyInstaller lo omite SIN avisar y el .exe falla con "Can't find a usable init.tcl".
if not tcltk_info.available:
    message = ("tkinter/Tcl-Tk no funciona en este Python: la ventana de arranque no se podría abrir. "
               "Define TCL_LIBRARY y TK_LIBRARY (carpeta tcl de tu Python) o reinstala Python con Tcl/Tk.")
    if sys.platform in ("win32", "darwin"):
        raise SystemExit("ERROR: " + message)
    print("AVISO: " + message + " (el lanzador usará el modo sin ventana)")

datas, binaries, hiddenimports = [], [], []

# Paquetes con archivos estáticos / binarios que hay que llevar completos
for pkg in ("streamlit", "plotly", "curl_cffi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# Zonas horarias (Windows no trae base de datos tz) y certificados TLS
datas += collect_data_files("tzdata") + collect_data_files("certifi")

# Metadatos que algunas librerías consultan en tiempo de ejecución
for pkg in ("streamlit", "plotly", "yfinance", "ccxt", "pandas", "numpy", "requests", "altair", "pyarrow"):
    try:
        datas += copy_metadata(pkg)
    except Exception:  # noqa: BLE001 - si no está instalado, no pasa nada
        pass

# Nuestro código: app.py lo ejecuta Streamlit como script, así que declaramos sus módulos.
# (Se listan desde el disco: collect_submodules corre aislado y no ve la carpeta del proyecto.)
from pathlib import Path

for package in ("engine", "strategies", "ui"):
    hiddenimports.append(package)
    hiddenimports += [f"{package}.{f.stem}" for f in sorted(Path(package).glob("*.py")) if f.stem != "__init__"]
hiddenimports += ["tradingview_ta", "streamlit.web.cli", "mplfinance", "six"]

# Archivos del proyecto que se leen desde disco
datas += [
    ("app.py", "."),
    ("ui/styles.css", "ui"),
    ("data/news_calendar.json", "data"),
    (".streamlit/config.toml", ".streamlit"),
    (".env.example", "."),
    ("assets/icon.png", "assets"),
]

a = Analysis(
    ["launcher.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "backtrader", "IPython", "notebook", "jupyter", "pandas_ta", "talib"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FuturesScope",
    console=False,  # Sin ventana negra de consola: se usa la ventana de arranque
    icon="assets/icon.icns" if sys.platform == "darwin" else "assets/icon.ico",
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="FuturesScope")

# macOS: empaquetar como FuturesScope.app (probado en Sonoma 14)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="FuturesScope.app",
        icon="assets/icon.icns",
        bundle_identifier="com.futuresscope.app",
        info_plist={
            "CFBundleName": "FuturesScope",
            "CFBundleDisplayName": "FUTURES-SCOPE",
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1.0.0",
            "LSMinimumSystemVersion": "14.0",
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": "BUILD 1 · GUS X SHAWTY X ALFREDOBONUS",
        },
    )
