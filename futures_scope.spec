# -*- mode: python ; coding: utf-8 -*-
"""
futures_scope.spec
==================
Receta de PyInstaller para construir FuturesScope.exe (carpeta autocontenida).

    pip install -r requirements.txt pyinstaller==6.22.3
    pyinstaller futures_scope.spec --noconfirm

Resultado: dist/FuturesScope/FuturesScope.exe (+ carpeta _internal).
Copia la carpeta dist/FuturesScope completa: el .exe necesita _internal a su lado.
Tu configuración (.env) y tus datos (data/) se crean junto al .exe en el primer arranque.
"""
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules, copy_metadata

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
    icon="assets/icon.ico",
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="FuturesScope")
