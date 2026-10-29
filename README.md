# DB-transfer

Прототип переноса структуры хранилища SAP BW в Greenplum-совместимую СУБД (Arenadata DB, Greengage).

Приложение читает CSV-выгрузки таблиц метаданных BW (как из транзакции SE16), строит модель хранилища, оценивает сложность переноса, генерирует SQL для Greengage, разворачивает объекты в локальной базе, прогоняет тестовые данные по учебному потоку «Продажи» и сверяет результат с эталоном. Полные требования описаны в [docs/tz-prototype.md](docs/tz-prototype.md).

## Что готово сейчас (шаг 1 из 5)

- Каркас проекта на Python 3.12 (uv, pytest, ruff) и CI в GitHub Actions.
- Целевая база Greengage 6 в Docker: `docker-compose.yml`.
- Синтетическая выгрузка метаданных BW по потоку «Продажи» и объектам вне потока: `demo/bw_export/`, контрольный список в `demo/bw_export/EXPECTED.md`. Состав колонок задан в `dbtransfer/rules/bw_tables.yaml`.
- Тестовые данные и эталоны: `demo/data/` (справочники, две загрузки плоского файла, эталоны для ZSD_A01 и ZSD_A02, испорченный эталон с 3 расхождениями) и пример из приложения Д ТЗ, посчитанный вручную: `demo/data/micro/`.
- Команда `dbtransfer db check` для проверки подключения к базе.

Импорт, отчёт о сложности, генерация SQL, развёртывание, сверка и веб-интерфейс появятся в следующих шагах (ТЗ, раздел 5).

## Что нужно установить

| Программа | Windows | Linux |
|---|---|---|
| Docker | [Docker Desktop](https://www.docker.com/products/docker-desktop/) с включённым WSL2 | Docker Engine и плагин Compose v2 (`docker compose version` должна работать) |
| uv | В PowerShell: `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 \| iex"` | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Git | [git-scm.com](https://git-scm.com/download/win) | `sudo apt install git` или аналог |

Python ставить отдельно не нужно: `uv sync` сам скачает Python 3.12.

Нужно 16 ГБ оперативной памяти и 20–30 ГБ свободного места. Образ Greengage есть для x86-64 и Apple Silicon.

## Запуск

### Linux и macOS

```bash
git clone https://github.com/befsrty/db-transfer.git
cd db-transfer
cp .env.example .env
docker compose up -d
uv sync
uv run dbtransfer db check
uv run pytest
```

### Windows (PowerShell)

```powershell
git clone https://github.com/befsrty/db-transfer.git
cd db-transfer
Copy-Item .env.example .env
docker compose up -d
uv sync
uv run dbtransfer db check
uv run pytest
```

Docker Desktop должен быть запущен перед `docker compose up -d`. Первый запуск скачивает образ (около 1 ГБ) и инициализирует базу, это занимает 1–3 минуты. Пока база не готова, `db check` сообщит, что подключиться не удалось; подожди и повтори.

Если всё в порядке, `db check` напечатает версию Greengage и строку «Права на создание схем есть». `pytest` запускает модульные тесты, им база не нужна.

### Проверка с живой базой

```bash
uv run pytest -m db
```

### Подключение из DBeaver

Тип подключения PostgreSQL (или Greenplum), хост `localhost`, порт из `.env` (по умолчанию 15432), база `demo`, пользователь `gpadmin`, пароль из `.env` (по умолчанию `student`).

## Частые проблемы

| Симптом | Что сделать |
|---|---|
| В `docker compose logs` бесконечно `getaddrinfo ...: Temporary failure in name resolution` | База создана контейнером с другим именем хоста (старая версия `docker-compose.yml`). Пересоздай базу: `docker compose down -v`, затем `docker compose up -d` (тестовые данные удалятся) |
| `port is already allocated` при `docker compose up` | Порт из `.env` занят. Поставь другой свободный `DBT_DB_PORT` (например, 25432) и выполни `docker compose up -d` ещё раз |
| `db check`: ошибка про пароль `gpadmin` или нечитаемые символы вместо текста | На порт отвечает не Greengage, а другой PostgreSQL на компьютере (так бывает, если в `.env` порт 5432). Проверь: `netstat -ano \| findstr <порт>` в Windows, `ss -ltnp \| grep <порт>` в Linux. Поставь свободный `DBT_DB_PORT`, выполни `docker compose up -d` и снова `db check` |
| `db check`: Connection refused | Контейнер не запущен или ещё инициализируется. Посмотри `docker compose ps` и `docker compose logs -f` |
| `db check`: password authentication failed | Пароль в `.env` поменяли после первого запуска. Пароль задаётся при создании базы: верни старый или пересоздай базу командой `docker compose down -v` (данные удалятся) |
| Docker Desktop на Windows не стартует | Включи WSL2: в PowerShell от администратора `wsl --install`, перезагрузи компьютер |
| `uv` не находится после установки | Открой новое окно терминала, чтобы обновился PATH |

## Управление базой

```bash
docker compose ps          # состояние
docker compose logs -f     # журнал
docker compose down        # остановить, данные сохранятся
docker compose down -v     # остановить и удалить данные
```

## Демо-набор

Демо-набор синтетический: реальной системы BW нет, состав полей таблиц собран по документации BW/4HANA и для настоящей выгрузки может потребовать правки `dbtransfer/rules/bw_tables.yaml`. Наборы создаются скриптами и закоммичены; тесты проверяют, что файлы совпадают с выводом скриптов.

```bash
uv run python demo/tools/make_bw_export.py   # метаданные BW -> demo/bw_export
uv run python demo/tools/make_demo_data.py   # данные и эталоны -> demo/data
```

Эталоны считаются на pandas по правилам ТЗ 4.3.5 независимо от SQL приложения: перезапись в standard ADSO с журналом изменений (пара X / ' ' со знаком минус у старых значений), суммирование в cumulative ADSO с сохранением нулевых строк. Сам расчёт проверен тестом на примере из приложения Д (`demo/data/micro/`).

В загрузке 1 даты в формате `ГГГГММДД`, пустая дата `00000000` и три повтора ключа (побеждает последняя запись). Загрузка 2 содержит по 10 корректировок каждого вида (сумма, месяц, клиент, валюта, повтор без изменений) и 50 новых позиций; вид каждой записан в `demo/data/load2_cases.csv`. Даты в загрузке 2 в формате `ДД.ММ.ГГГГ`, пустая дата — пустая строка. Суммы везде с десятичной запятой.

## Структура

```
dbtransfer/          пакет приложения
  rules/             bw_tables.yaml: какие таблицы и колонки BW ожидает импорт
  config.py          правила и подключение из .env
  db.py              подключение к базе
  cli.py             команды dbtransfer
demo/
  bw_export/         синтетические CSV метаданных BW
  data/              справочники, загрузки, эталоны
  tools/             генераторы демо-набора
  project.yaml       демо-проект
tests/
  unit/              без базы
  integration/       с Greengage (pytest -m db)
docs/                ТЗ и документация
```
