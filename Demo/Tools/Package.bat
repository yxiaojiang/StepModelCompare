@echo off
setlocal
for %%I in ("%~dp0..") do set "DEMO=%%~fI"

call "%~dp0Compile.bat"
if errorlevel 1 exit /b 1

"%DEMO%\.venv\Scripts\python.exe" "%DEMO%\src\packaging\build_studio.py" package %*
exit /b %ERRORLEVEL%
