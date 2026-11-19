@echo off
setlocal
for %%I in ("%~dp0..") do set "DEMO=%%~fI"
set "PYTHON=%DEMO%\.venv\Scripts\python.exe"

if not exist "%PYTHON%" (
    echo Creating the Python 3.13 environment...
    py -3.13 -m venv "%DEMO%\.venv"
    if errorlevel 1 (
        echo ERROR: Install 64-bit Python 3.13 and its Windows launcher.
        exit /b 1
    )
)

"%PYTHON%" -c "import sys,struct; sys.exit(sys.version_info[:2] != (3,13) or struct.calcsize('P') != 8)"
if errorlevel 1 (
    echo ERROR: The Demo environment must use 64-bit Python 3.13.
    exit /b 1
)

"%PYTHON%" -c "import PyInstaller, webview, OCP, scipy, igl, fastapi, uvicorn, python_multipart" >nul 2>&1
if errorlevel 1 (
    echo Installing build dependencies...
    "%PYTHON%" -m pip install -r "%DEMO%\src\requirements.txt" -r "%DEMO%\src\studio-requirements.txt"
    if errorlevel 1 exit /b 1
)

"%PYTHON%" "%DEMO%\src\packaging\build_studio.py" compile
exit /b %ERRORLEVEL%
