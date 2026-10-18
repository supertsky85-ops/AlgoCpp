@echo off
setlocal
title AlgoCpp
cd /d "%~dp0AlgoCpp"
if errorlevel 1 (
    echo.
    echo [AlgoCpp] Cannot find the AlgoCpp folder next to this file.
    echo Please extract the whole repository and keep this file beside the AlgoCpp folder.
    pause
    exit /b 1
)
if not exist "start.bat" (
    echo.
    echo [AlgoCpp] AlgoCpp\start.bat is missing.
    echo Please re-download or re-extract the repository.
    pause
    exit /b 1
)
call "start.bat"
