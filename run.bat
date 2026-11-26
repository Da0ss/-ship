@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
set VENV=%LOCALAPPDATA%\bcc-validator-venv

where py >nul 2>nul
if errorlevel 1 (
  echo [!] Python не найден. Установите Python 3.12 с https://www.python.org/downloads/
  echo     и при установке отметьте галочку "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
  echo Создаю окружение Python...
  py -3.12 -m venv "%VENV%" 2>nul || py -3.11 -m venv "%VENV%" 2>nul || py -3 -m venv "%VENV%"
)
"%VENV%\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
  echo [!] Нужен Python 3.11 или новее. Установите Python 3.12 с https://www.python.org/downloads/
  rmdir /s /q "%VENV%"
  pause
  exit /b 1
)

echo Устанавливаю зависимости (первый раз 1-2 минуты)...
"%VENV%\Scripts\python.exe" -m pip install -q --disable-pip-version-check -r requirements.txt

if /i "%~1"=="models" goto models
if /i "%~1"=="test" goto test
goto run

:models
"%VENV%\Scripts\python.exe" -m validator --list-models > reports\models.txt 2>&1
type reports\models.txt
goto done

:test
"%VENV%\Scripts\python.exe" -m pytest -q
goto done

:run
set INPUT=%~1
set OUT=reports
set GOLDEN=tests\golden\requirements.golden.json
if "%INPUT%"=="" set INPUT=docs\requirements.md
if not "%~1"=="" (
  set OUT=reports\%~n1
  set GOLDEN=tests\golden\%~n1.golden.json
)
if not exist "%OUT%" mkdir "%OUT%"
echo Проверяю %INPUT% (5-15 минут, окно не закрывайте)...
"%VENV%\Scripts\python.exe" -m validator "%INPUT%" "%OUT%\report.md" --json "%OUT%\report.json" 2> "%OUT%\run.log"
if errorlevel 1 (
  type "%OUT%\run.log"
  echo.
  echo [!] Прогон не завершился. Подробности в %OUT%\run.log
  goto done
)
type "%OUT%\run.log"
if exist "%GOLDEN%" (
  "%VENV%\Scripts\python.exe" -m validator.evaluate "%OUT%\report.json" "%GOLDEN%" > "%OUT%\metrics.txt" 2>&1
  type "%OUT%\metrics.txt"
)
echo Отчёт: %OUT%\report.md и %OUT%\report.html

:done
echo.
echo Готово. Можно закрыть окно.
pause
