# DB-transfer

Прототип переноса структуры хранилища SAP BW в Greenplum-совместимую СУБД (Arenadata DB, Greengage).

Приложение читает CSV-выгрузки таблиц метаданных BW (как из транзакции SE16), строит модель хранилища, оценивает сложность переноса, генерирует SQL для Greengage, разворачивает объекты в локальной базе, прогоняет тестовые данные по учебному потоку «Продажи» и сверяет результат с эталоном. Полные требования описаны в [docs/tz-prototype.md](docs/tz-prototype.md).

## Что готово сейчас (шаги 1–3 из 5)

- **Шаг 1.** Каркас проекта на Python 3.12 (uv, pytest, ruff), CI в GitHub Actions, база Greengage 6 в Docker, синтетическая выгрузка метаданных BW (`demo/bw_export/`, контрольный список `EXPECTED.md`), тестовые данные и эталоны (`demo/data/`), команда `dbtransfer db check`.
- **Шаг 2.** Импорт выгрузок BW в модель хранилища, проверка файлов, отчёт о сложности в Excel, состав переноса и lineage полей в командной строке.
- **Шаг 3.** Генерация пакета SQL для Greengage: таблицы справочников, DataSource и всех видов ADSO, функции активации и трансформаций, служебные таблицы.

Развёртывание из приложения, загрузка файлов, прогон, сверка и веб-интерфейс появятся в следующих шагах (ТЗ, раздел 5).

## Как пользоваться

Команды работают с проектом — папкой с `project.yaml`. По умолчанию это текущая папка, а в корне репозитория — демо-проект `demo/`; другой проект задаётся `-p <папка>`.

```
uv run dbtransfer tables                          # какие таблицы BW выгрузить и как (подробно: docs/bw-export.md)
uv run dbtransfer import                          # импорт demo/bw_export -> demo/model.json, demo/out/import_check.xlsx
uv run dbtransfer report                          # отчёт о сложности -> demo/out/complexity.xlsx
uv run dbtransfer scope --from ZSD_A02            # состав переноса: поток от ZSD_A02 вниз до источников
uv run dbtransfer lineage ZSD_A02.0NETVAL_INV     # откуда пришло поле
uv run dbtransfer lineage ZSALES_FF.NETVAL --up   # куда идёт поле
uv run dbtransfer generate                        # пакет SQL -> demo/out/sql
uv run dbtransfer generate --scope                # только состав переноса из project.yaml
uv run dbtransfer init C:\work\my-bw            # новый проект для своей выгрузки
```

Пример lineage на демо-наборе:

```
ZSD_A02.0NETVAL_INV [dbt_mart.zsd_a02_active.netval_inv]
└─ ← прямое присвоение (ZSD_A01 → ZSD_A02) ZSD_A01.0NETVAL_INV [dbt_dwh.zsd_a01_active.netval_inv]
   ├─ ← формула NETVAL - DISCOUNT (ZSALES_FF → ZSD_A01) ZSALES_FF.NETVAL [dbt_stg.zsales_ff.netval]
   └─ ← формула NETVAL - DISCOUNT (ZSALES_FF → ZSD_A01) ZSALES_FF.DISCOUNT [dbt_stg.zsales_ff.discount]
```

В квадратных скобках — где поле будет лежать в Greengage (схема.таблица.колонка). Правила обхода: формула ветвит путь по своим полям, чтение атрибута — в поле-ключ и справочник, константа завершает путь, рутина обрывает его, а шаг через трансформацию с конечной рутиной помечается «возможно изменено рутиной».

### Пакет SQL

`generate` пишет в `out/sql/` файлы в порядке развёртывания:

| Файл | Что внутри |
|---|---|
| `010_schemas.sql` | Схемы `dbt_stg`, `dbt_md`, `dbt_dwh`, `dbt_mart`, `dbt_meta` |
| `020_meta.sql` | Журнал загрузок, журнал развёртываний, результаты сверок, последовательности номеров |
| `030_md.sql` | Атрибуты и тексты признаков (`dbt_md.material_attr`, `dbt_md.material_text`, …) |
| `040_datasources.sql` | Входные таблицы DataSource с колонками `load_id`, `record_no` |
| `050_adso.sql` | Таблицы ADSO: входящая, активные данные, журнал изменений по виду ADSO |
| `060_activation.sql` | Функции активации: `dbt_dwh.activate_zsd_a01(load_id)` и т. д. |
| `070_transformations.sql` | Функции трансформаций: `dbt_dwh.tr_zsales_ff__zsd_a01(load_id)` и т. д. |
| `deploy_all.sql` | Всё одним файлом для `psql` (первая строка — команда `psql`, DBeaver её не понимает) |
| `drop_all.sql` | Удаление схем прототипа вместе с данными |

Развернуть пакет в Greengage без приложения (в шаге 4 это будет делать `dbtransfer deploy`): в DBeaver открыть по очереди файлы `010`…`070` и выполнить каждый как скрипт (Alt+X) или, если установлен клиент PostgreSQL, `psql -h localhost -p 15432 -U gpadmin -d demo -f demo/out/sql/deploy_all.sql`. Повторный запуск ничего не ломает: таблицы создаются с `IF NOT EXISTS`, функции заменяются.

Трансформация с ABAP-рутиной или правилом, которое прототип не переводит (в демо — `ZSD_A01 → ZSD_A03`), создаётся как заготовка: в комментарии черновик SQL и код рутины, а сама функция падает с сообщением «требует ручной доработки». Типы, хранение и ключи распределения задаются в `dbtransfer/rules/type_rules.yaml`; свой `type_rules.yaml` рядом с `project.yaml` переопределяет их для проекта, раздел `overrides` — для отдельного объекта. Параметр `--dialect postgres` убирает из SQL особенности Greenplum (хранение и распределение) для обычного PostgreSQL.

Нормы трудоёмкости для отчёта лежат в `dbtransfer/rules/effort_norms.yaml`, правила имён в базе — в `dbtransfer/rules/naming.yaml`.

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

Тесты разворачивают сгенерированный SQL в отдельных схемах `dbtest_*` (демо-схемы `dbt_*` не трогаются) и прогоняют через функции пример из приложения Д ТЗ: активные данные `ZSD_A01`, журнал изменений и `ZSD_A02` должны совпасть с `demo/data/micro/` строка в строку.

Тесты генерации сравнивают SQL с эталонными файлами в `tests/golden/sql/`. Если SQL поменялся намеренно, обновите эталоны командой `UPDATE_GOLDEN=1 uv run pytest tests/unit/test_generator.py` (в PowerShell: `$env:UPDATE_GOLDEN=1; uv run pytest tests/unit/test_generator.py; Remove-Item Env:UPDATE_GOLDEN`) и просмотрите разницу в `git diff`.

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
  rules/             bw_tables.yaml, naming.yaml, effort_norms.yaml, type_rules.yaml
  model/             модель метаданных (pydantic), сохраняется в model.json
  importers/         импорт CSV-выгрузок SE16
  analysis/          граф и состав переноса, уровни и трудоёмкость, Excel-отчёты, lineage
  generator/         генерация SQL: типы, формулы, таблицы, активация, трансформации, шаблоны Jinja2
  project.py         проект: project.yaml, model.json, out/
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
  golden/sql/        эталонный пакет SQL демо-проекта
  integration/       с Greengage (pytest -m db)
docs/                ТЗ и документация
```
