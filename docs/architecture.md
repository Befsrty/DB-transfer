# Архитектура DB-transfer

Как устроено приложение, где лежат правила и что менять, чтобы добавить новый источник или новую целевую СУБД. Требования — в [tz-prototype.md](tz-prototype.md), раздел 4.5.

## Путь данных

```
CSV-выгрузки SE16 ──import──▶ Model (model.json) ──assess──▶ Report (Excel)
                                   │
                                   ├──lineage──▶ путь поля (консоль, интерфейс, Excel)
                                   │
                                   └──generate──▶ Package (SQL по группам, out/sql/)
                                                      │
                                                      └──deploy──▶ Greengage: схемы dbt_*, deploy_log
                                                                      │
                                    CSV-данные ──load──▶ сырые таблицы ──run──▶ ADSO ──reconcile──▶ протокол
```

Каждая стрелка — публичная функция модуля. Командная строка (`cli.py`) и веб-интерфейс (`ui/`) только вызывают эти функции и показывают результат, своей логики переноса в них нет.

## Модули

| Модуль | Что делает | Главные функции и классы |
|---|---|---|
| `model/bw.py` | Модель хранилища BW на pydantic: InfoObject, ADSO, DataSource, трансформации с правилами и рутинами, DTP, прочие и неактивные объекты, проблемы импорта. Сохраняется в `model.json`, хеш содержимого — `content_hash()` | `Model`, `Adso`, `DataSource`, `Transformation`, `Rule`, `ObjectRef` |
| `importers/` | Чтение CSV SE16 (кодировка, разделитель, заголовки), проверка колонок по `bw_tables.yaml`, сборка модели. Не знает о целевой СУБД | `import_bw_export(folder)`, `SE16_HELP` |
| `analysis/graph.py` | Граф объектов на networkx, состав переноса «от объекта вниз» | `build_graph`, `scope_from` |
| `analysis/assessment.py` | Уровни автоматизации 1–3 с причинами, трудоёмкость по нормам `effort_norms.yaml` | `assess(model) → Report` |
| `analysis/lineage.py` | Lineage поля вверх и вниз по правилам трансформаций | `lineage(model, obj, field, DOWN/UP) → Node`, `render` |
| `analysis/excel.py` | Excel-отчёты о проверке импорта и о сложности | `write_import_check`, `write_complexity` |
| `naming.py` | Имена схем, таблиц и колонок в целевой СУБД по `naming.yaml`; слой ADSO (dwh или mart) | `schema`, `table`, `column`, `adso_layer` |
| `generator/types.py` | Перевод типов, хранение и распределение по `type_rules.yaml` (с переопределением в проекте) | `TypeRules.load(project_dir)` |
| `generator/tables.py` | Описание таблиц: справочники, DataSource, роли ADSO (входящая, активная, журнал изменений) | `TableBuilder`, `TableDef` |
| `generator/formula_sql.py` | Перевод формул BW в SQL (приложение В ТЗ) | `translate(formula, resolve)` |
| `generator/transform.py` | SQL трансформаций: правила, чтение атрибутов, время, фильтр DTP, заготовки для рутин | `TransformBuilder` |
| `generator/package.py` | Сборка пакета по группам `010_schemas` … `070_transformations`, шаблоны Jinja2 в `templates/` | `generate(model, rules, dialect, scope) → Package` |
| `db.py` | Подключение psycopg 3, проверка прав, выбор диалекта по версии сервера | `connect`, `check_connection`, `server_dialect` |
| `deploy.py` | План (создать, оставить, заменить, пересоздать), выполнение одной транзакцией, журнал `deploy_log` и его копия в `out/`, защита от развёртывания изменённой модели без пересоздания | `make_plan`, `deploy`, `save_log` |
| `runtime.py` | Загрузка CSV через COPY в сырую таблицу, проверка значений в SQL, перенос во входную таблицу; прогон загрузки по составу переноса; откат | `load_file`, `run_load`, `rollback_load`, `flow_steps` |
| `reconcile.py` | Сверка активных данных ADSO с эталоном по ключу и показателям, суммы, протокол Excel и `reconcile_result`, сверка нижнего слоя при расхождениях | `reconcile` |
| `scenario.py` | Сквозной сценарий `run-demo` | `run_demo` |
| `project.py`, `config.py` | Проект (`project.yaml`, `model.json`, `out/`), правила по умолчанию, подключение из `.env` | `find_project`, `open_project`, `load_db_settings` |
| `cli.py` | Команды Typer | `app` |
| `ui/` | Streamlit: `app.py` — вкладки, `schema.py` — схема потоков в DOT, `cards.py` — таблицы карточек и lineage (без Streamlit, покрыты тестами) | `flow_dot`, `object_fields`, `lineage_excel` |

Зависимости идут в одну сторону: `model` ← `importers`, `analysis` ← `generator` ← `deploy` ← `runtime` ← `reconcile` ← `scenario` ← `cli`, `ui`. Модель ни от чего не зависит, импорт не знает о базе, генератор не подключается к базе.

## Правила (меняются без кода)

| Файл | Что задаёт |
|---|---|
| `dbtransfer/rules/bw_tables.yaml` | Какие таблицы BW нужны, их колонки и ключи. Пометка `hypothesis: true` — состав взят из документации и требует проверки на настоящей выгрузке |
| `dbtransfer/rules/type_rules.yaml` | Тип ABAP → тип целевой СУБД, начальные значения, хранение (heap или AO колоночное со сжатием) по роли таблицы, ключ распределения. Файл с тем же именем в папке проекта переопределяет ключи |
| `dbtransfer/rules/naming.yaml` | Шаблоны имён схем и таблиц |
| `dbtransfer/rules/effort_norms.yaml` | Нормы трудоёмкости ручной работы, часы на объект |
| `<проект>/project.yaml` | Название, префикс схем, папки выгрузки и данных, состав переноса по умолчанию (`scope_from`) |
| `<проект>/reconcile.yaml` | Соответствие колонок эталона полям ADSO и допуск сверки |

Изменение правил меняет хеш пакета SQL, и `deploy` попросит `--recreate`, если таблицы уже развёрнуты по старым правилам.

## Служебные таблицы

Схема `<префикс>_meta`: `deploy_log` (каждое развёртывание по объектам), `load_log` (загрузки: файл, хеш, строки, статус loaded / activated / failed / откат), `reconcile_result` (итоги сверок). Номера загрузок и развёртываний — последовательности в той же схеме. Схема `<префикс>_meta` не удаляется при `--recreate`, кроме журнала загрузок.

## Как добавить источник

Источник — то, из чего строится модель. Сейчас это CSV-выгрузки SAP BW.

1. Создать модуль в `dbtransfer/importers/`, например `oracle_dict.py`, с функцией `import_<источник>(...) → Model`. Она заполняет те же классы модели: таблицы источника становятся `DataSource` или `Adso`, связи — `Transformation` с правилами, проблемы — `model.issue(...)`.
2. Если понятий источника не хватает в модели (например, представления или хранимые процедуры), добавить класс в `model/bw.py` или использовать `OtherObject` — тогда объект попадёт в отчёт о сложности с ручной трудоёмкостью, но без генерации SQL.
3. Добавить команду в `cli.py` (или параметр `--source` у `import`) и кнопку на вкладке «Импорт».
4. Тесты: синтетический набор в `tests/` и проверка, что модель строится, а `generate` даёт ожидаемый SQL.

Анализ, генерация, развёртывание и сверка работают с моделью и менять их не нужно.

## Как добавить целевую СУБД

Цель — то, для чего генерируется SQL. Сейчас поддержаны диалекты `greenplum` (Greengage, Arenadata DB, Greenplum 6) и `postgres` (без `DISTRIBUTED BY` и параметров хранения).

1. Добавить имя в `DIALECTS` в `generator/package.py`.
2. Различия в DDL описываются в шаблонах `generator/templates/*.sql.j2` через флаг диалекта (сейчас `greenplum`). Для СУБД с другим синтаксисом (например, ClickHouse) лучше завести отдельную папку шаблонов и выбирать её по диалекту в `Generator`.
3. Если нужны другие типы или хранение — отдельный файл правил типов или секция в `type_rules.yaml`.
4. В `db.server_dialect` научить определять СУБД по строке версии, если подключение идёт тем же драйвером. Для другого драйвера — функция подключения в `db.py`.
5. Функции активации и трансформаций написаны на PL/pgSQL. Для СУБД без него их нужно генерировать как набор SQL-запросов, а прогон в `runtime.run_load` — выполнять по шагам.
6. Проверка: golden-файлы для нового диалекта в `tests/golden/` и интеграционный тест с базой в CI.

## Тесты

- `tests/unit/` — без базы: разбор CSV, модель, уровни, lineage, перевод типов и формул, пакет SQL против `tests/golden/sql/`, сверка на DataFrame, интерфейс через `streamlit.testing`.
- `tests/integration/` — с базой (`pytest -m db`): развёртывание, повторное развёртывание, загрузка, прогон, сверка, пример приложения Д. В CI запускаются на Greengage 6.32 как service-контейнере.
