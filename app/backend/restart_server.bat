@echo off
cd /d "D:\Obsidian\repository\Work\03 Projects\AI群聊发现器\app\backend"
call .venv\Scripts\activate.bat
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
