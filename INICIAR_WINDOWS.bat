@echo off
REM ==========================================================================
REM  FUTURES-SCOPE - Arranque con doble clic en Windows
REM  La primera vez crea el entorno e instala dependencias (unos minutos).
REM ==========================================================================
chcp 65001 >nul
title FUTURES-SCOPE
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo  No se encontro Python 3.11 o superior.
  echo  Instalalo desde https://www.python.org/downloads/ marcando "Add python.exe to PATH".
  echo.
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Creando entorno virtual...
  python -m venv .venv
  if errorlevel 1 ( pause & exit /b 1 )
  echo [2/2] Instalando dependencias ^(solo la primera vez^)...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Error instalando dependencias. Revisa tu conexion a internet.
    pause
    exit /b 1
  )
)

if not exist ".env" copy ".env.example" ".env" >nul

REM pythonw = sin ventana de consola: se abre la ventana de arranque de FUTURES-SCOPE
start "" ".venv\Scripts\pythonw.exe" launcher.py
