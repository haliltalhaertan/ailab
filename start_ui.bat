@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=.venv\Scripts\python.exe

if not exist "%PY%" (
  echo [HATA] .venv bulunamadi: %PY%
  pause
  exit /b 1
)

for /f %%h in ('git rev-parse --short HEAD') do set HEAD=%%h
for /f %%b in ('git branch --show-current') do set BR=%%b
echo ==========================================================
echo  ailab arayuzu   dal: %BR%   commit: %HEAD%
echo  Tarayici otomatik acilir (http://localhost:8501).
echo  Kapatmak icin bu pencerede Ctrl+C ya da pencereyi kapat.
echo  Calisan deneyler worker'da surer, pencere kapansa da olmez.
echo ==========================================================
echo.

"%PY%" -m streamlit run app.py --server.port 8501 --browser.gatherUsageStats false
pause
