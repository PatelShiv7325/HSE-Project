@echo off
setlocal
cd /d "%~dp0"
echo ===== Building: Global HSE Associates - Client =====

where python >nul 2>nul || (echo Python not found. Install it from python.org first. & pause & exit /b 1)
if not exist venv python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install pyinstaller || (pause & exit /b 1)

venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed ^
  --name GlobalHSE-Client --icon hse.ico client_app.py || (echo PyInstaller failed. & pause & exit /b 1)

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  "%ISCC%" installer_client.iss && echo Installer created in the installer_output folder.
) else (
  echo Program built in dist\GlobalHSE-Client.
  echo To make the Setup file: install Inno Setup 6 from jrsoftware.org, then run this file again.
)
echo.
pause
