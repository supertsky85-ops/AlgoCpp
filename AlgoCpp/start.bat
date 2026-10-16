@echo off
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 goto missing
python algocpp.py
if errorlevel 1 pause
exit /b
:missing
echo Python 3 with tkinter is required. Install Python and add it to PATH.
pause
