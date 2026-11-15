@echo off
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
    echo Please install the project dependencies first. See docs/windows-install.md
    pause
    exit /b 1
)
".venv\Scripts\python.exe" "scripts\local_launch.py" login
pause
