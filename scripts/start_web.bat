@echo off
rem Start xianshouself web app (if not running) and open orders page in default browser.
rem 服务以无窗口方式运行（run_web.vbs），桌面上不会有多余的黑窗口。
cd /d "%~dp0\.."
netstat -ano | findstr /C:"127.0.0.1:5000" | findstr /C:"LISTENING" >nul
if errorlevel 1 (
    wscript "%~dp0run_web.vbs"
    timeout /t 3 /nobreak >nul
)
start "" "http://127.0.0.1:5000/orders"
