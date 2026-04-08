@echo off
cd /d "D:\Obsidian\repository\Work\03 Projects\AI群聊发现器\app\backend"
set PYTHONPATH=.
.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8000
