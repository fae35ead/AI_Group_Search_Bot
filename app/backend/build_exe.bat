@echo off
setlocal

set "BACKEND_DIR=%~dp0"
set "FRONTEND_DIR=%BACKEND_DIR%..\frontend"
set "PYTHON_EXE=%BACKEND_DIR%.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo Python virtual environment is missing: %PYTHON_EXE%
  exit /b 1
)

pushd "%FRONTEND_DIR%"
call npm run build
if errorlevel 1 (
  popd
  exit /b 1
)
popd

pushd "%BACKEND_DIR%"
taskkill /IM ai-group-discovery.exe /F >nul 2>nul
if exist "dist\ai-group-discovery.exe" del /F /Q "dist\ai-group-discovery.exe"

call "%PYTHON_EXE%" -m pip install -r requirements.txt pyinstaller
if errorlevel 1 (
  popd
  exit /b 1
)

call "%PYTHON_EXE%" -m playwright install chromium
if errorlevel 1 (
  popd
  exit /b 1
)

call "%PYTHON_EXE%" -m PyInstaller --noconfirm --clean ai-group-discovery.spec
set "BUILD_EXIT=%ERRORLEVEL%"
popd

exit /b %BUILD_EXIT%
