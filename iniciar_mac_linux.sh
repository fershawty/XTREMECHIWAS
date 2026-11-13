#!/usr/bin/env bash
# ============================================================================
#  FUTURES-SCOPE - Arranque en macOS / Linux
#    chmod +x iniciar_mac_linux.sh && ./iniciar_mac_linux.sh
#  (En macOS puedes renombrarlo a .command para abrirlo con doble clic.)
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

PY="$(command -v python3.11 || command -v python3.12 || command -v python3.13 || command -v python3 || true)"
if [ -z "$PY" ]; then
  echo "No se encontró Python 3.11+. Instálalo desde https://www.python.org/downloads/"
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "[1/2] Creando entorno virtual con $PY ..."
  "$PY" -m venv .venv
  echo "[2/2] Instalando dependencias (solo la primera vez)..."
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

[ -f .env ] || cp .env.example .env
exec .venv/bin/python launcher.py "$@"
