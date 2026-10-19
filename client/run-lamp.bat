@echo off
chcp 65001 >nul
title DSH Status Lamp
cd /d "%~dp0"
rem Prefer pythonw (no console window); fall back to python.
where pythonw >nul 2>nul && (
    start "" pythonw "lamp.py" --url http://192.168.1.50:3099/events
) || (
    python "lamp.py" --url http://192.168.1.50:3099/events
)
