@echo off
cd /d "%~dp0"
echo Studio One Project Tools debug launcher
echo Current folder: %CD%
echo.

python --version
echo.

python main.py

echo.
echo Process finished with exit code %ERRORLEVEL%
echo.
if exist startup_error_log.txt (
    echo ===== startup_error_log.txt =====
    type startup_error_log.txt
    echo.
)
if exist error_log.txt (
    echo ===== error_log.txt =====
    type error_log.txt
    echo.
)
pause
