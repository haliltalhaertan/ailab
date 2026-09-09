@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PY=.venv\Scripts\python.exe
set BRANCH=fix/audit-cleanup-baselines
set EXPECTED=42ae19f9cbb778af82606987aa085b83eb188a1b
set DAY=2026-09-03

echo ==========================================================
echo  ailab canli baseline kosusu  (PR #26 / madde 6)
echo ==========================================================
echo.

if not exist "%PY%" (
  echo [HATA] .venv bulunamadi: %PY%
  goto :fail
)

findstr /B "OPENROUTER_API_KEY=" .env >nul 2>&1
if errorlevel 1 (
  echo [HATA] .env icinde OPENROUTER_API_KEY satiri yok. Once ekle, sonra tekrar calistir.
  goto :fail
)
echo [OK] .env anahtari bulundu.

echo.
echo [1/4] Dala geciliyor: %BRANCH%
git fetch origin || goto :fail
git switch %BRANCH% || goto :fail
git reset --hard origin/%BRANCH% || goto :fail
for /f %%h in ('git rev-parse HEAD') do set HEAD=%%h
if /i not "%HEAD%"=="%EXPECTED%" (
  echo [UYARI] HEAD beklenen commit degil: %HEAD%
  echo         Dal guncellenmis olabilir; devam ediliyor.
)
echo [OK] HEAD = %HEAD%

echo.
echo [2/4] Kosu 1: gpt-4o-mini, low effort (protokol testi, sentlik, birkac dakika)
"%PY%" experiments\theorem_baseline_probe.py --iterations 2 --model openai/gpt-4o-mini --reasoning-effort low --report-copy docs\baselines\%DAY%-gpt-4o-mini.md
if errorlevel 1 (
  echo [UYARI] Kosu 1 hata koduyla bitti; rapor yine de yazilmis olabilir.
)

echo.
echo [3/4] Kosu 2: uretim konfigurasyonu (deepseek/kimi/gemini/glm, 10-30 dk, ~0.3-1 USD)
"%PY%" experiments\theorem_baseline_probe.py --iterations 2 --agent-config experiments\baseline_production_agents.json --max-tokens 8000 --report-copy docs\baselines\%DAY%-production.md
if errorlevel 1 (
  echo [UYARI] Kosu 2 hata koduyla bitti; rapor yine de yazilmis olabilir.
)

echo.
echo [4/4] Raporlar:
dir /b docs\baselines 2>nul
echo.
echo Ozet (ilk satirlar):
for %%f in (docs\baselines\%DAY%-*.md) do (
  echo --- %%f
  "%PY%" -c "import sys;print(''.join(open(sys.argv[1],encoding='utf-8').readlines()[:14]))" "%%f"
)

echo.
set /p ANSWER=Raporlar commit edilip push edilsin mi? [E/H]
if /i "%ANSWER%"=="E" (
  git add docs\baselines || goto :fail
  git commit -m "docs(baselines): add %DAY% live baseline reports" || goto :fail
  git push origin %BRANCH% || goto :fail
  echo [OK] Push edildi. GitHub'da PR #26'yi draft'tan cikar, #20'yi kapat.
) else (
  echo Commit atlandi. Raporlar docs\baselines altinda duruyor.
)

echo.
echo Bitti.
pause
exit /b 0

:fail
echo.
echo [DURDU] Yukaridaki hatayi duzeltip tekrar calistir.
pause
exit /b 1
