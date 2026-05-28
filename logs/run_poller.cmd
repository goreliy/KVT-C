@echo off
cd /d "%~dp0.."
"D:\Python\Python310\python.exe" -m poller.app --port 5001 >> "logs\poller.out.log" 2>> "logs\poller.err.log"
