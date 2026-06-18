@echo off
:: start.bat — Launch VEmbed Video Combiner on Windows.
:: Double-click this file to start the server and open the browser.

cd /d "%~dp0"
echo Starting VEmbed Video Combiner...

:: Activate the virtual environment
call venv\Scripts\activate.bat

:: Start Flask in the background
start "VEmbed Server" /B python app.py

:: Wait a moment for the server to boot
timeout /t 3 /nobreak >nul

:: Open the browser
start http://localhost:5000

echo.
echo Server is running at http://localhost:5000
echo Close this window to stop the server.
pause
