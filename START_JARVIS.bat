@echo off
title J.A.R.V.I.S. MK VI
cd /d "%~dp0"

echo.
echo   ██████╗ █████╗    ██╗ █████╗ ██████╗ ██╗   ██╗ █████╗
echo  ██╔═══██╗██╔══██╗  ██║██╔══██╗██╔══██╗██║   ██║██╔══██╗
echo  ██║   ██║███████║  ██║███████║██║  ██║██║   ██║███████║
echo  ██║   ██║██╔══██║  ██║██╔══██║██║  ██║╚██╗ ██╔╝██╔══██║
echo  ╚██████╔╝██║  ██║  ██║██║  ██║██████╔╝ ╚████╔╝ ██║  ██║
echo   ╚═════╝ ╚═╝  ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝   ╚═══╝  ╚═╝  ╚═╝
echo.
echo   Just A Rather Very Intelligent System - MK VI
echo   Local machine control  ^|  127.0.0.1 only
echo.

set PY="C:\Users\Henil Patel\AppData\Local\Programs\Python\Python312\python.exe"
if not exist %PY% (
  echo [ERROR] Python 3.12 not found at %PY%
  echo         Edit this file and set PY to your python.exe path.
  pause & exit /b 1
)

start "" http://127.0.0.1:8420
%PY% jarvis_server.py
pause
