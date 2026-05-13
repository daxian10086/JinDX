@echo off
title JinDX Proxy
echo Starting JinDX Proxy...
start "" /B "%~dp0proxy-backend.exe"
echo.
echo Proxy is starting on ports:
echo   HTTP/WS:  http://127.0.0.1:8080
echo   Admin UI: http://127.0.0.1:8090
echo   TLS:      https://127.0.0.1:8444
echo   CONNECT:  127.0.0.1:8443
echo.
echo Opening admin panel in browser...
timeout /t 3 /nobreak >nul
start http://127.0.0.1:8090
echo.
echo Proxy is running. Close this window to stop.
pause >nul
