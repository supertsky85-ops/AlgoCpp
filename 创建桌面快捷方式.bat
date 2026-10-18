@echo off
setlocal
set "ALGO_TARGET=%~dp0AlgoCpp\start.bat"
set "ALGO_FOLDER=%~dp0AlgoCpp"
if not exist "%ALGO_TARGET%" (
    echo.
    echo [AlgoCpp] Cannot find AlgoCpp\start.bat next to this file.
    echo Please extract the whole repository and keep the AlgoCpp folder beside this file.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws = New-Object -ComObject WScript.Shell; $desktop = $ws.SpecialFolders('Desktop'); $s = $ws.CreateShortcut((Join-Path $desktop 'AlgoCpp.lnk')); $s.TargetPath = $env:ALGO_TARGET; $s.WorkingDirectory = $env:ALGO_FOLDER; $s.Description = 'AlgoCpp - Local C++ IDE'; $s.Save()"
if errorlevel 1 (
    echo.
    echo [AlgoCpp] Could not create the desktop shortcut.
    echo Try right-clicking the AlgoCpp launcher and choosing Send to - Desktop ^(create shortcut^).
    pause
    exit /b 1
)
echo.
echo [AlgoCpp] Desktop shortcut created: AlgoCpp
echo Keep this folder in place; if you move it, run this file again to refresh the shortcut.
pause
