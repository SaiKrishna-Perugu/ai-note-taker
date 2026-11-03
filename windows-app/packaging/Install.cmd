@echo off
rem Double-click to install AI Note Taker for the current user (no administrator rights needed).
title Install AI Note Taker
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-AINoteTaker.ps1" %*
if errorlevel 1 (
    echo.
    echo Installation did not complete. See the message above.
) else (
    echo.
    echo Done.
)
pause
