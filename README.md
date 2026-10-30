# DB-transfer

Прототип переноса структуры хранилища SAP BW в Greenplum-совместимую СУБД (Arenadata DB, Greengage).

Приложение читает CSV-выгрузки таблиц метаданных BW (как из транзакции SE16), строит модель хранилища, оценивает сложность переноса, генерирует SQL для Greengage, разворачивает объекты в локальной базе, прогоняет тестовые данные по учебному потоку «Продажи» и сверяет результат с эталоном. Полные требования описаны в [docs/tz-prototype.md](docs/tz-prototype.md).

## Что готово (шаги 1–5)

- **Шаг 1.** Каркас проекта на Python 3.12 (uv, pytest, ruff), CI в GitHub Actions, база Greengage 6 в Docker, синтетическая выгрузка метаданных BW (`demo/bw_export/`, контрольный список `EXPECTED.md`), тестовые данные и эталоны (`demo/data/`), команда `dbtransfer db check`.
- **Шаг 2.** Импорт выгрузок BW в модель хранилища, проверка файлов, отчёт о сложности в Excel, состав переноса и lineage полей в командной строке.
- **Шаг 3.** Генерация пакета SQL для Greengage: таблицы справочников, DataSource и всех видов ADSO, функции активации и трансформаций, служебные таблицы.
- **Шаг 4.** Развёртывание пакета в базе, загрузка CSV, прогон загрузки по потоку, сверка с эталоном и сквозной сценарий `run-demo`.
- **Шаг 5.** Веб-интерфейс: схема потоков с подсветкой lineage, карточки объектов и трансформаций, все действия командной строки на шести вкладках. Сценарий защиты — [docs/demo-script.md](docs/demo-script.md), устройство приложения — [docs/architecture.md](docs/architecture.md), протокол приёмки — [docs/acceptance.md](docs/acceptance.md).

## Веб-интерфейс

**Windows, без командной строки.** Двойной щелчок по `start-ui.bat` в папке проекта: он запустит Docker Desktop, если тот не запущен, поднимет базу, дождётся её готовности и откроет интерфейс в браузере. Окно с чёрным экраном не закрывать, пока работаешь: закрыл — интерфейс остановился. Ярлык на рабочий стол создаёт `make-shortcut.bat` (запустить один раз). Если папку проекта перенесли, запусти `make-shortcut.bat` заново.

**Из терминала** (Windows, Linux, macOS):

```
uv run dbtransfer ui
```

Откроется `http://localhost:8501` (другой порт — `--port`, другой проект — `-p`, другой файл подключения — `--env-file`). Остановить — Ctrl+C в терминале.

| Вкладка | Что там |
|---|---|
| Импорт | Какие таблицы BW выгрузить, импорт папки с CSV, найденные проблемы по файлам, Excel-отчёт о проверке |
| Схема | Схема потоков с фильтрами по типам и слоям; рамка узла — уровень автоматизации, красное ребро — трансформация с рутиной. В списке «Карточка» выбирается объект или трансформация: поля, таблицы в Greengage, правила, код рутины. Кнопки «Откуда пришло» и «Куда идёт» показывают lineage поля и подсвечивают путь на схеме |
| Сложность | Сводка по типам и уровням, список объектов с причинами, рутины ABAP, Excel |
| SQL | Пакет SQL по файлам или по объекту, скачивание архивом |
| Развёртывание | План, развёртывание (пересоздание — с подтверждением), загрузка CSV из папки данных или своего файла, прогон по номеру загрузки, журнал загрузок, кнопка «Запустить демо целиком» |
| Сверка | Объект, эталон, допуск; итог по статусам, суммы по показателям, расхождения, протокол Excel |

Вкладки «Импорт», «Схема», «Сложность» и «SQL» работают без базы.

## Демо одной командой

При запущенном Greengage (`docker compose up -d`):

```
uv run dbtransfer run-demo
```

Сценарий импортирует демо-выгрузки, строит отчёт о сложности, генерирует SQL, разворачивает его с пересозданием схем `dbt_*`, загружает справочники и две загрузки продаж, прогоняет их по потоку `ZSALES_FF → ZSD_A01 → ZSD_A02`, сверяет оба ADSO с эталонами и печатает итог:

```
Итог
  Объектов уровня 1        26 из 33 (79%)
  Ручная работа по нормам  28 ч
  Строк SQL                818
  Объектов БД              38
  Загрузок                 ZSALES_FF_load1.csv: 503 строк, ZSALES_FF_load2.csv: 100 строк
  Сверка ZSD_A01           совпало 550 из 550
  Сверка ZSD_A02           совпало 142 из 142
Демо прошло: цифры совпали с эталоном
```

Протоколы сверки лежат в `demo/out/reconcile_*.xlsx`, журналы — в схеме `dbt_meta` (смотреть в DBeaver).

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
uv run dbtransfer deploy                          # развернуть пакет в базе (журнал dbt_meta.deploy_log)
uv run dbtransfer deploy --recreate               # пересоздать таблицы, тестовые данные удалятся
uv run dbtransfer load 0MATERIAL demo/data/md_0MATERIAL_attr.csv   # справочник
uv run dbtransfer load ZSALES_FF demo/data/ZSALES_FF_load1.csv     # плоский файл, выводит load_id
uv run dbtransfer run 5                           # прогон загрузки 5 по составу переноса
uv run dbtransfer rollback 5                      # откат ещё не прогнанной загрузки
uv run dbtransfer reconcile ZSD_A02 demo/data/etalon_ZSD_A02.csv   # сверка, протокол в out/
uv run dbtransfer init C:\work\my-bw            # новый проект для своей выгрузки
uv run dbtransfer ui                              # веб-интерфейс на http://localhost:8501
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

Развернуть пакет без приложения тоже можно: в DBeaver открыть по очереди файлы `010`…`070` и выполнить каждый как скрипт (Alt+X) или, если установлен клиент PostgreSQL, `psql -h localhost -p 15432 -U gpadmin -d demo -f demo/out/sql/deploy_all.sql`. Повторный запуск ничего не ломает: таблицы создаются с `IF NOT EXISTS`, функции заменяются.

Трансформация с ABAP-рутиной или правилом, которое прототип не переводит (в демо — `ZSD_A01 → ZSD_A03`), создаётся как заготовка: в комментарии черновик SQL и код рутины, а сама функция падает с сообщением «требует ручной доработки». Типы, хранение и ключи распределения задаются в `dbtransfer/rules/type_rules.yaml`; свой `type_rules.yaml` рядом с `project.yaml` переопределяет их для проекта, раздел `overrides` — для отдельного объекта. Параметр `--dialect postgres` убирает из SQL особенности Greenplum (хранение и распределение) для обычного PostgreSQL.

Нормы трудоёмкости для отчёта лежат в `dbtransfer/rules/effort_norms.yaml`, правила имён в базе — в `dbtransfer/rules/naming.yaml`.

### Развёртывание, загрузка, прогон, сверка

- `deploy` создаёт схемы, таблицы и функции одной транзакцией и пишет результат по каждому объекту в `dbt_meta.deploy_log`. Повторный запуск ничего не ломает. Если модель или правила изменились с прошлого развёртывания, а таблицы уже есть, `deploy` остановится и попросит `--recreate`: без пересоздания таблицы остались бы старыми. `--recreate` удаляет схемы с данными и журнал загрузок; журналы развёртываний и сверок остаются.
- `load` грузит CSV в два шага: файл копируется в сырую таблицу `dbt_stg.<имя>_raw` (все колонки текстовые), затем запрос проверяет значения и переносит строки в рабочую таблицу. Десятичная запятая, даты `ГГГГММДД` и `ДД.ММ.ГГГГ`, пустая дата и `00000000` понимаются. Если хоть одно значение не приводится к типу, загрузка отменяется целиком и печатаются первые 20 ошибок с номером строки, полем и значением. Повторная загрузка того же файла требует `--force`.
- `run` прогоняет загрузку DataSource по составу переноса (`scope_from` в `project.yaml`): трансформации и активации, одной транзакцией. Если в потоке есть заготовка с рутиной, прогон не начнётся. Загрузки прогоняются строго по возрастанию номера, повторный прогон отклоняется.
- `reconcile` сверяет активные данные ADSO с эталоном по ключу и каждому показателю (допуск 0,01): «совпало», «расхождение», «нет в ADB», «нет в эталоне». Протокол — в Excel (`out/reconcile_<объект>_<время>.xlsx`) и в `dbt_meta.reconcile_result`. Если на верхнем слое есть расхождения, а рядом лежит эталон нижнего слоя (`etalon_ZSD_A01.csv`), сверяется и он. Колонки эталона называются по InfoObject; другое соответствие задаётся в `reconcile.yaml` проекта:

```yaml
ZSD_A02:
  columns: {Клиент: 0SOLD_TO, Месяц: 0CALMONTH}
  tolerance: 0.01
```

Команды работы с базой берут подключение из `.env` (другой файл — `--env-file`). Диалект SQL выбирается по серверу: Greengage/Greenplum или обычный PostgreSQL.

## Известные ограничения

- Желательные требования ТЗ, которые не сделаны: генератор синтетического набора метаданных заданного размера для проверки производительности (ФП-2.6) и сопоставление текстовых заголовков SE16 («Имя таблицы») с техническими именами (ФП-2.7). Выгрузку нужно делать с техническими именами колонок, как описано в [docs/bw-export.md](docs/bw-export.md).
- Схема потоков статична: объект выбирается в списке «Карточка», клик по узлу ничего не делает (так задано в ТЗ, 4.4.1).
- Состав колонок таблиц BW собран по документации, а не по настоящей выгрузке; пометки `hypothesis: true` в `dbtransfer/rules/bw_tables.yaml`.
- ABAP-рутины не переводятся: генерируется заготовка, прогон через неё блокируется.

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
uv run dbtransfer run-demo
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
uv run dbtransfer run-demo
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
| `uv sync`: «Отказано в доступе» или os error 396 | Проект лежит в папке OneDrive. Перенеси его в обычную папку, например `C:\projects\db-transfer`, и задай `$env:UV_LINK_MODE = "copy"` |
| `dbtransfer ui`: порт 8501 занят | `uv run dbtransfer ui --port 8502` |
| В интерфейсе не видно изменений после `import` в терминале | Обнови страницу (F5): интерфейс перечитывает `model.json` по времени изменения файла |

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
  deploy.py          развёртывание пакета и журнал deploy_log
  runtime.py         загрузка CSV, прогон, откат
  reconcile.py       сверка с эталоном и протокол
  scenario.py        сквозной сценарий run-demo
  project.py         проект: project.yaml, model.json, out/
  config.py          правила и подключение из .env
  db.py              подключение к базе
  cli.py             команды dbtransfer
  ui/                веб-интерфейс Streamlit: app.py, схема потоков (schema.py), карточки (cards.py)
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
