@echo off
REM ============================================================
REM  Vigil Desktop — Build Script
REM  Produces:  dist\Vigil\Vigil.exe
REM ============================================================

echo.
echo ============================================================
echo   Vigil Desktop Build
echo ============================================================
echo.

REM -- Check Python --
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ and add to PATH.
    pause
    exit /b 1
)

REM -- Install build dependencies --
echo Installing dependencies...
pip install -r requirements.txt >nul 2>&1
if errorlevel 1 (
    echo ERROR: pip install failed. Check your Python installation.
    pause
    exit /b 1
)

REM -- Clean previous build --
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

REM -- Build --
echo.
echo Building Vigil.exe ...
echo.
python -m PyInstaller vigil.spec --noconfirm

if errorlevel 1 (
    echo.
    echo ERROR: Build failed. Check the output above.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   BUILD COMPLETE
echo.
echo   Output:  dist\Vigil\Vigil.exe
echo.
echo   To run:  dist\Vigil\Vigil.exe
echo   To distribute: copy the entire dist\Vigil folder.
echo ============================================================
echo.
pause
