@echo off
echo Starting Video Stitcher Setup...
echo.

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not installed! Please install Python 3.8 or higher.
    echo You can download Python from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM Run the setup script
python setup.py
if errorlevel 1 (
    echo Setup failed! Please check the error messages above.
    pause
    exit /b 1
)

echo.
echo Setup completed successfully!
echo You can now run the application using: python api/app.py
pause 