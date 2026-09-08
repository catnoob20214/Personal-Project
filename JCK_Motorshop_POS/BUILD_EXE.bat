@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"
set "LOG=build_log.txt"

REM Clear any leftover TCL_LIBRARY / TK_LIBRARY pointing at an old/removed
REM Python install (these can be set permanently by older launcher scripts
REM and make PyInstaller fail with "Tk data/library directory ... could
REM not be collected"). Clearing them lets PyInstaller auto-detect the
REM correct Tcl/Tk that ships with whichever Python actually runs it.
set "TCL_LIBRARY="
set "TK_LIBRARY="

echo ============================================== > "%LOG%"
echo JCK Motorshop POS - build log >> "%LOG%"
echo ============================================== >> "%LOG%"

echo Looking for a working Python installation (this can take a few seconds)...
set "PYEXE="

REM 1) The "py" launcher, if installed (usually at C:\Windows\py.exe)
py -3 -c "print(1)" >nul 2>>"%LOG%"
if !errorlevel! equ 0 set "PYEXE=py -3"

REM 2) Search common per-user install locations for any python.exe
if not defined PYEXE (
    for /f "delims=" %%F in ('dir /s /b "%LOCALAPPDATA%\python.exe" 2^>nul') do (
        if not defined PYEXE (
            "%%F" -c "print(1)" >nul 2>>"%LOG%"
            if !errorlevel! equ 0 set "PYEXE=%%F"
        )
    )
)

REM 3) Search Program Files locations
if not defined PYEXE (
    for /f "delims=" %%F in ('dir /s /b "%PROGRAMFILES%\python.exe" 2^>nul') do (
        if not defined PYEXE (
            "%%F" -c "print(1)" >nul 2>>"%LOG%"
            if !errorlevel! equ 0 set "PYEXE=%%F"
        )
    )
)

REM 4) A couple of common manual-install locations
if not defined PYEXE if exist "C:\Python314\python.exe" (
    "C:\Python314\python.exe" -c "print(1)" >nul 2>>"%LOG%"
    if !errorlevel! equ 0 set "PYEXE=C:\Python314\python.exe"
)

REM 5) Plain "python" on PATH, as a last resort (may be the Store stub)
if not defined PYEXE (
    python -c "print(1)" >nul 2>>"%LOG%"
    if !errorlevel! equ 0 set "PYEXE=python"
)

if not defined PYEXE (
    echo.
    echo [ERROR] Could not automatically find a working Python installation.
    echo.
    echo Please find it manually:
    echo   1. Open VS Code with this project.
    echo   2. Click the Python version/interpreter name in the bottom-right
    echo      status bar ^(or press Ctrl+Shift+P and type "Python: Select
    echo      Interpreter"^).
    echo   3. It shows the full path to the interpreter VS Code is using -
    echo      copy that exact path.
    echo   4. Tell Claude that exact path so BUILD_EXE.bat can be pointed
    echo      at it directly.
    echo.
    pause
    exit /b 1
)

echo Using Python: %PYEXE%
echo Using Python: %PYEXE% >> "%LOG%"

echo Installing/upgrading required packages (this can take a minute)...
%PYEXE% -m pip install --upgrade pyinstaller matplotlib >>"%LOG%" 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] pip install failed. See build_log.txt for the full error.
    echo This usually means no internet connection, or pip is not available.
    echo.
    pause
    exit /b 1
)

echo.
echo Building JCK Motorshop POS.exe ... (this can take 1-3 minutes)
%PYEXE% -m PyInstaller --onefile --windowed --noconfirm --name "JCK Motorshop POS" ^
  --icon "jck_logo.ico" ^
  --add-data "jck_logo.ico;." ^
  --add-data "jck_logo.png;." ^
  jck_motorshop_pos.py >>"%LOG%" 2>&1

if %errorlevel% neq 0 (
    echo.
    echo [ERROR] PyInstaller build failed. See build_log.txt for the full error.
    echo.
    pause
    exit /b 1
)

if not exist "dist\JCK Motorshop POS.exe" (
    echo.
    echo [ERROR] Build finished but the exe was not found in dist\.
    echo See build_log.txt for details.
    echo.
    pause
    exit /b 1
)

echo.
echo ==========================================================
echo SUCCESS! Your exe is here:
echo   %cd%\dist\JCK Motorshop POS.exe
echo.
echo Copy that .exe together with jck_motorshop_pos.db, jck_logo.ico,
echo and jck_logo.png into one folder - that folder is what you send
echo to the other laptop.
echo ==========================================================
echo.
pause
