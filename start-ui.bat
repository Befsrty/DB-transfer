@echo off
rem Запуск DB-transfer двойным щелчком: база Greengage в Docker и веб-интерфейс.
rem Окно не закрывать: пока оно открыто, интерфейс работает. Остановить — закрыть окно или Ctrl+C.
chcp 65001 >nul
cd /d "%~dp0"
set UV_LINK_MODE=copy
if not exist .env copy .env.example .env >nul
title DB-transfer

docker info >nul 2>&1
if errorlevel 1 (
    echo Docker не запущен, запускаю Docker Desktop...
    if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
    for /l %%i in (1,1,36) do (
        timeout /t 5 /nobreak >nul
        docker info >nul 2>&1 && goto docker_ready
    )
    echo Docker так и не запустился. Запусти Docker Desktop вручную и открой этот файл ещё раз.
    pause
    exit /b 1
)
:docker_ready

echo Запускаю базу Greengage...
docker compose up -d
if errorlevel 1 (
    echo Не удалось запустить контейнер. Подробности: docker compose logs
    pause
    exit /b 1
)

echo Жду, пока база будет готова: после перезагрузки компьютера это до 3 минут...
for /l %%i in (1,1,40) do (
    uv run dbtransfer db check >nul 2>&1 && goto db_ready
    timeout /t 5 /nobreak >nul
)
echo База не ответила. Проверь: uv run dbtransfer db check
pause
exit /b 1
:db_ready

echo База готова. Открываю веб-интерфейс http://localhost:8501
uv run dbtransfer ui %*
pause
