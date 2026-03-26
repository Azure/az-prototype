@echo off
setlocal

echo ========================================
echo  Azure CLI Extension - Build ^& Install
echo ========================================
echo.

:: Check Python is available
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Python is not installed or not in PATH.
    echo Download from https://www.python.org/downloads/
    exit /b 1
)

:: Ensure build tools are installed
echo [1/4] Ensuring build tools are installed...
python -m pip install --upgrade build setuptools wheel >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Failed to install build tools.
    exit /b 1
)

:: Clean previous builds
echo [2/4] Cleaning previous builds...
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
for /d %%d in (*.egg-info) do rmdir /s /q "%%d"
for /d /r azext_prototype %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"

:: Pre-compute policy embeddings (requires sentence-transformers at build time only)
echo [3/4] Computing policy embeddings...
python -m pip install sentence-transformers >nul 2>&1
python scripts\compute_embeddings.py
if %errorlevel% neq 0 (
    echo ERROR: Embedding computation failed.
    exit /b 1
)

:: Build the wheel (--no-isolation avoids PermissionError on temp-env cleanup)
echo [4/4] Building wheel...
python -m build --wheel --no-isolation
if %errorlevel% neq 0 (
    echo ERROR: Build failed.
    exit /b 1
)

for %%f in (dist\az_prototype-*.whl) do set "WHL_FILE=%%f"

echo.
echo ========================================
echo  Build complete!
echo  Wheel: %WHL_FILE%
echo.
echo  Install with:
echo    az extension remove --name prototype 2^>nul ^& az extension add --source %WHL_FILE% --yes
echo ========================================
endlocal
