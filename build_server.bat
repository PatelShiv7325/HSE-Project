@echo off
setlocal
cd /d "%~dp0"
echo ===== Building: Global HSE Associates - Server =====

where python >nul 2>nul || (echo Python not found. Install it from python.org first. & pause & exit /b 1)
if not exist venv python -m venv venv
venv\Scripts\python.exe -m pip install --upgrade pip
venv\Scripts\python.exe -m pip install -r requirements.txt pyinstaller || (pause & exit /b 1)

venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --windowed ^
  --name GlobalHSE-Server --icon hse.ico ^
  --add-data "app\templates;app\templates" --add-data "app\static;app\static" ^
  --collect-submodules app --collect-all playwright ^
  --hidden-import config --hidden-import email_service --hidden-import whatsapp_service ^
  --hidden-import sqlalchemy.dialects.sqlite ^
  desktop_app.py || (echo PyInstaller failed. & pause & exit /b 1)

call :makeinstaller installer_server.iss
echo.
pause
exit /b 0

:makeinstaller
set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  "%ISCC%" %1 && echo Installer created in the installer_output folder.
) else (
  echo Program built in dist\GlobalHSE-Server.
  echo To make the Setup file: install Inno Setup 6 from jrsoftware.org, then run this file again.
)
exit /b 0
