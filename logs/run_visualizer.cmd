@echo off
cd /d "%~dp0.."
"D:\Python\Python310\python.exe" -m visualizer.app >> "logs\visualizer.out.log" 2>> "logs\visualizer.err.log"
