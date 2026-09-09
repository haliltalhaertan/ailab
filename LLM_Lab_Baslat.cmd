@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run portal.py --server.address=127.0.0.1 --server.port=8501 --browser.gatherUsageStats=false
if errorlevel 1 pause
