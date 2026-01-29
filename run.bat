@echo off
title SEO BOT
echo ========================================
echo  SEO BOT
echo ========================================
echo.

:: Change to the script directory
cd /d "%~dp0"

:: Check if virtual environment exists
if not exist "venv\" (
    echo [INFO] Virtual environment not found. Creating...
    python -m venv venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [INFO] Virtual environment created successfully.
    echo.

    :: Activate and install dependencies
    call venv\Scripts\activate.bat
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo [INFO] Dependencies installed successfully.
    echo.
) else (
    echo [INFO] Virtual environment found.
    call venv\Scripts\activate.bat
)

echo [INFO] Starting the application...
echo ========================================
echo.

:: Run the main script
python script\main.py

:: Keep window open if there's an error
if errorlevel 1 (
    echo.
    echo [ERROR] Application exited with an error.
    pause
)
