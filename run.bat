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
if "%INPUT%"=="" set INPUT=docs\requirements.md
echo Проверяю %INPUT% (5-15 минут, окно не закрывайте)...
"%VENV%\Scripts\python.exe" -m validator "%INPUT%" reports\report.md --json reports\report.json 2> reports\run.log
if errorlevel 1 (
  type reports\run.log
  echo.
  echo [!] Прогон не завершился. Подробности в reports\run.log
  goto done
)
type reports\run.log
"%VENV%\Scripts\python.exe" -m validator.evaluate reports\report.json tests\golden\requirements.golden.json > reports\metrics.txt 2>&1
type reports\metrics.txt

:done
echo.
echo Готово. Можно закрыть окно.
pause
