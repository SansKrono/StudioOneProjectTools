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

echo Installing/updating PyInstaller...
python -m pip install --upgrade pip
python -m pip install --upgrade pyinstaller

echo.
echo Cleaning old build folders...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.
echo Building EXE...
python -m PyInstaller --noconfirm --clean StudioOneProjectTools.spec
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
echo IMPORTANT:
echo Keep es.exe next to StudioOneProjectTools.exe.
echo The app uses bundled es.exe automatically.
echo.
pause
