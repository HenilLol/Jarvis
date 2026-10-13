@echo off
title J.A.R.V.I.S. MK VIII
cd /d "%~dp0"

echo.
echo   ██████╗ █████╗    ██╗ █████╗ ██████╗ ██╗   ██╗ █████╗
echo  ██╔═══██╗██╔══██╗  ██║██╔══██╗██╔══██╗██║   ██║██╔══██╗
echo  ██║   ██║███████║  ██║███████║██║  ██║██║   ██║███████║
echo  ██║   ██║██╔══██║  ██║██╔══██║██║  ██║╚██╗ ██╔╝██╔══██║
echo  ╚██████╔╝██║  ██║  ██║██║  ██║██████╔╝ ╚████╔╝ ██║  ██║
echo   ╚═════╝ ╚═╝  ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝   ╚═══╝  ╚═╝  ╚═╝
echo.
echo   Just A Rather Very Intelligent System - MK VIII
echo   Voice + AI brain + machine control ^| 127.0.0.1 only
echo.

set PY="C:\Users\Henil Patel\AppData\Local\Programs\Python\Python312\python.exe"
if not exist %PY% (
  echo [ERROR] Python 3.12 not found at %PY%
  echo         Edit this file and set PY to your python.exe path.
  pause & exit /b 1
)

REM -- AI brain: start Ollama if the binary exists but server isn't running
if exist "%~dp0ollama\ollama.exe" (
  curl -s -m 2 http://localhost:11434/api/version >nul 2>&1
  if errorlevel 1 (
    echo [BRAIN] starting Ollama...
    set OLLAMA_MODELS=E:\JARVIS\models\ollama
    start "JARVIS-BRAIN" /min "%~dp0ollama\ollama.exe" serve
    timeout /t 4 /nobreak >nul
  ) else (
    echo [BRAIN] Ollama already running.
  )
) else (
  echo [BRAIN] Ollama not found - AI chat disabled ^(commands still work^).
)

echo [JARVIS] bringing systems online...
start "" http://127.0.0.1:8420
%PY% jarvis_server.py
pause
