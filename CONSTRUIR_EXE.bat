@echo off
REM ==========================================================================
REM  FUTURES-SCOPE - Construir FuturesScope.exe en tu propio PC (Windows)
REM  Alternativa: descárgalo ya compilado desde la pestaña "Actions" de GitHub.
REM ==========================================================================
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
  if errorlevel 1 ( echo Necesitas Python 3.11+ en el PATH. & pause & exit /b 1 )
)
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller==6.22.3
if errorlevel 1 ( pause & exit /b 1 )

".venv\Scripts\python.exe" -m PyInstaller futures_scope.spec --noconfirm
if errorlevel 1 ( echo La compilacion fallo. & pause & exit /b 1 )

echo.
echo  Listo: dist\FuturesScope\FuturesScope.exe
echo  Copia la carpeta dist\FuturesScope completa (el .exe necesita _internal a su lado).
explorer dist\FuturesScope
pause
