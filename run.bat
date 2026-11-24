@echo off
cd /d "%~dp0"
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"
echo Starting MathRex AI Live Server on http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000
"%PY%" backend\server.py
pause
