@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo Studio One Project Tools v2 - Windows EXE build
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found in PATH.
    echo Install Python 3.11+ and enable "Add Python to PATH".
    pause
    exit /b 1
)

echo Creating build virtual environment in .venv-build ...
if not exist ".venv-build\Scripts\python.exe" python -m venv .venv-build
if errorlevel 1 (
    echo ERROR: Could not create the virtual environment.
    pause
    exit /b 1
)

echo Installing build requirements...
".venv-build\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR: Could not install build requirements.
    pause
    exit /b 1
)

echo.
echo Running tests...
".venv-build\Scripts\python.exe" -m unittest discover -s tests
if errorlevel 1 (
    echo ERROR: Tests failed. Not building.
    pause
    exit /b 1
)

echo.
echo Cleaning old build folders...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo Building EXE...
".venv-build\Scripts\python.exe" -m PyInstaller --noconfirm --clean StudioOneProjectTools.spec
if errorlevel 1 (
    echo.
    echo ERROR: Build failed.
    pause
    exit /b 1
)

echo.
echo Build complete.
echo EXE folder:
echo %CD%\dist\StudioOneProjectTools
echo.
if not exist es.exe (
    echo NOTE: es.exe was not bundled. Missing-file search will use es.exe from
    echo PATH or Program Files\Everything, or the path set in Settings.
    echo.
)
pause
