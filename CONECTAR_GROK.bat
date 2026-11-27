@echo off
REM ==========================================================================
REM  FG VIEW - Conectar con GROK en un clic (Windows)
REM  Abre FG VIEW y el tunel de Cloudflare. Tu solo copias la direccion.
REM  Ponlo en la misma carpeta que FGView.exe y dale doble clic.
REM ==========================================================================
chcp 65001 >nul
title FG VIEW - Conectar con Grok
cd /d "%~dp0"
set PUERTO=8765

echo.
echo  ===============================================================
echo   FG VIEW  -  CONECTAR CON GROK
echo  ===============================================================
echo.

REM --- 1) FG VIEW -----------------------------------------------------------
if not exist "FGView.exe" (
  echo  [X] No encuentro FGView.exe en esta carpeta:
  echo      %cd%
  echo.
  echo      Pon este archivo junto a FGView.exe y vuelve a intentarlo.
  echo.
  pause
  exit /b 1
)
echo  [1/3] Abriendo FG VIEW...
start "" "FGView.exe"

REM --- 2) cloudflared -------------------------------------------------------
set CF=cloudflared
where cloudflared >nul 2>nul
if errorlevel 1 (
  if exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" (
    set CF="%ProgramFiles(x86)%\cloudflared\cloudflared.exe"
  ) else if exist "%ProgramFiles%\cloudflared\cloudflared.exe" (
    set CF="%ProgramFiles%\cloudflared\cloudflared.exe"
  ) else (
    echo.
    echo  [X] No encuentro cloudflared, que es el programa del tunel.
    echo      Instalalo una sola vez con este comando en PowerShell:
    echo.
    echo        winget install --id Cloudflare.cloudflared
    echo.
    echo      Luego cierra PowerShell, abrelo de nuevo y vuelve a dar doble clic aqui.
    echo.
    pause
    exit /b 1
  )
)

echo  [2/3] Abriendo el tunel hacia Grok...
echo.
echo  ---------------------------------------------------------------
echo   AHORA HAZ ESTO, EN ESTE ORDEN:
echo.
echo    1. En FG VIEW:  menu izquierdo  ^>  GROK DESK (MCP)
echo       Pulsa  "Encender servidor"  (debe quedar en ENCENDIDO).
echo.
echo    2. En la ventana del tunel que se abre, copia la direccion
echo       que termina en  .trycloudflare.com
echo.
echo    3. Pegala en FG VIEW, en  "Direccion del tunel".
echo       Debajo aparece la DIRECCION DEL CONECTOR (termina en /mcp/...).
echo       Copiala.
echo.
echo    4. En grok.com  ^>  Connectors  ^>  New Connector  ^>  Custom:
echo       Nombre: FG VIEW     URL: la direccion del conector.  Guardar.
echo.
echo    5. En el chat de Grok escribe:
echo       "Usa fg_mejores_criptos y dime las 5 mejores para operar ahora"
echo.
echo   NO CIERRES la ventana del tunel mientras uses Grok.
echo   Cada vez que la cierres y la vuelvas a abrir, la direccion CAMBIA
echo   y hay que pegar la nueva en FG VIEW y en grok.com.
echo  ---------------------------------------------------------------
echo.
echo  [3/3] Iniciando tunel en el puerto %PUERTO%...
echo.

%CF% tunnel --url http://127.0.0.1:%PUERTO%

echo.
echo  El tunel se cerro. Si fue sin querer, vuelve a dar doble clic aqui.
pause
