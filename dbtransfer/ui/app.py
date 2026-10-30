"""Веб-интерфейс DB-transfer (ТЗ, 4.4.1). Запуск: dbtransfer ui (streamlit run этого файла).

Шесть вкладок: Импорт, Схема, Сложность, SQL, Развёртывание, Сверка. Интерфейс вызывает только
публичные функции модулей; логика схемы и карточек — в dbtransfer.ui.schema и dbtransfer.ui.cards.
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from dbtransfer.analysis.assessment import TYPE_ORDER, Report, assess
from dbtransfer.analysis.excel import write_complexity, write_import_check
from dbtransfer.analysis.graph import ScopeError
from dbtransfer.analysis.lineage import DOWN, UP, LineageError, lineage, render
from dbtransfer.config import load_bw_tables, load_db_settings
from dbtransfer.db import DbError, check_connection, connect, server_dialect
from dbtransfer.deploy import ACTION_TEXT, DeployError, deploy, make_plan
from dbtransfer.generator import DIALECTS, GROUPS, RulesError, TypeRules, generate
from dbtransfer.importers.bw_csv import SE16_HELP, ImportFailed, import_bw_export
from dbtransfer.model import Adso, DataSource, Model, Severity
from dbtransfer.project import Project, ProjectError, find_project, open_project
from dbtransfer.reconcile import MATCH, STATUSES, ReconcileError, reconcile
from dbtransfer.runtime import LoadError, RunError, load_file, run_load
from dbtransfer.scenario import ScenarioError, data_files, run_demo
from dbtransfer.ui import cards
from dbtransfer.ui.schema import LAYER_LABELS, LEVEL_COLOR, TYPE_LABELS, flow_dot, lineage_highlight

SEVERITY_TEXT = {Severity.ERROR: "ошибка", Severity.WARNING: "предупреждение", Severity.INFO: "сведения"}


# --- проект и модель ---

def _arg(name: str, env: str) -> str | None:
    """Параметр запуска: `dbtransfer ui` передаёт его после `--`, тесты — через переменную окружения."""
    args = sys.argv[1:]
    if name in args and args.index(name) + 1 < len(args):
        return args[args.index(name) + 1]
    return os.environ.get(env)


def _default_project() -> str:
    try:
        explicit = _arg("--project", "DBT_PROJECT")
        return str(open_project(Path(explicit)).root if explicit else find_project(None).root)
    except ProjectError:
        return "demo"


@st.cache_data(show_spinner=False)
def _load_model(path: str, mtime: float) -> Model:
    return Model.load(Path(path))


@st.cache_data(show_spinner=False)
def _report(path: str, mtime: float) -> Report:
    return assess(_load_model(path, mtime))


def _model(project: Project) -> Model | None:
    if not project.model_path.is_file():
        return None
    return _load_model(str(project.model_path), project.model_path.stat().st_mtime)


def _report_of(project: Project) -> Report:
    return _report(str(project.model_path), project.model_path.stat().st_mtime)


def _rules(project: Project) -> TypeRules | None:
    try:
        return TypeRules.load(project.root)
    except RulesError as e:
        st.error(f"type_rules.yaml: {e}")
        return None


def _env_file() -> Path:
    return Path(st.session_state.get("env_file", ".env"))


def _connect():
    return connect(load_db_settings(_env_file()))


def _need_model(model: Model | None) -> bool:
    if model is None:
        st.info("В проекте ещё нет модели. Импортируйте выгрузки на вкладке «Импорт».")
        return False
    return True


def _download(label: str, path: Path, key: str) -> None:
    if path.is_file():
        st.download_button(label, path.read_bytes(), file_name=path.name, key=key)


# --- боковая панель ---

def sidebar() -> Project | None:
    st.sidebar.title("DB-transfer")
    st.sidebar.caption("Перенос структуры SAP BW в Greengage / Arenadata DB")
    folder = st.sidebar.text_input("Папка проекта", value=st.session_state.get("project_dir", _default_project()))
    st.session_state["project_dir"] = folder
    if "env_file" not in st.session_state:
        st.session_state["env_file"] = _arg("--env-file", "DBT_ENV_FILE") or ".env"
    st.sidebar.text_input("Файл подключения", key="env_file")
    try:
        project = open_project(Path(folder))
    except ProjectError as e:
        st.sidebar.error(str(e))
        return None
    st.sidebar.markdown(f"**{project.config.name or project.root.name}**  \nсхемы `{project.config.schema_prefix}_*`")
    st.sidebar.markdown(f"Состав переноса: поток от **{project.config.scope_from or 'не задан'}**")
    # Статус подключения проверяется при открытии и по кнопке, а не на каждое действие
    recheck = st.sidebar.button("Проверить подключение")
    if recheck or "db_status" not in st.session_state:
        try:
            info = check_connection(load_db_settings(_env_file()))
            st.session_state["db_status"] = ("ok", "База: " + info.version.split(" on ")[0], "")
        except DbError as e:
            st.session_state["db_status"] = ("error", "Нет подключения к базе", str(e))
    status, text, detail = st.session_state["db_status"]
    if status == "ok":
        st.sidebar.success(text)
    else:
        st.sidebar.error(text)
        with st.sidebar.expander("Что делать"):
            st.write(detail)
    return project


# --- вкладки ---

def tab_import(project: Project) -> None:
    st.subheader("Импорт выгрузок BW")
    with st.expander("Какие таблицы BW выгрузить и как (SE16)"):
        specs = load_bw_tables()
        st.dataframe(pd.DataFrame([{
            "Таблица": s.name, "Группа": s.group, "Нужна": "обязательно" if s.required else "желательно",
            "Зачем": s.purpose, "Поля": ", ".join(s.required_columns),
        } for s in specs.values()]), hide_index=True, width="stretch")
        st.code(SE16_HELP, language=None)
    folder = st.text_input("Папка с CSV-выгрузками", value=str(project.bw_export_dir))
    if st.button("Импортировать", type="primary"):
        try:
            model = import_bw_export(Path(folder))
        except ImportFailed as e:
            st.error(str(e))
            return
        project.save_model(model)
        write_import_check(model, project.out_dir / "import_check.xlsx")
        st.cache_data.clear()
        st.success(f"Импортировано: ADSO {len(model.adsos)}, DataSource {len(model.datasources)}, "
                   f"трансформаций {len(model.transformations)}, InfoObject {len(model.info_objects)}")
    model = _model(project)
    if model is None:
        return
    st.markdown(f"Модель: `{project.model_path}`, импорт {model.imported_at:%d.%m.%Y %H:%M}"
                if model.imported_at else f"Модель: `{project.model_path}`")
    issues = [i for i in model.issues if i.severity != Severity.INFO]
    if issues:
        st.markdown("**Проблемы по файлам**")
        st.dataframe(pd.DataFrame([{"Важность": SEVERITY_TEXT[i.severity], "Файл": i.file or "",
                                    "Строка": i.line, "Объект": i.object or "", "Что не так": i.message}
                                   for i in issues]), hide_index=True, width="stretch")
    else:
        st.success("Ошибок и предупреждений нет.")
    if model.inactive:
        st.caption("Пропущены неактивные объекты: " + ", ".join(f"{o.name} ({o.type})" for o in model.inactive))
    _download("Скачать отчёт о проверке (Excel)", project.out_dir / "import_check.xlsx", "dl_import")


def _object_names(model: Model) -> list[str]:
    names = sorted(model.adsos) + sorted(d.name for d in model.datasources.values())
    names += sorted(o.name for o in model.others if o.type.value in ("HCPR", "ELEM"))
    return names


def tab_schema(project: Project, model: Model, report: Report) -> None:
    rules = _rules(project)
    left, right = st.columns([3, 2])
    with right:
        st.markdown("**Фильтры схемы**")
        types = st.multiselect("Типы объектов", list(TYPE_LABELS.values()), default=list(TYPE_LABELS.values()))
        layers = st.multiselect("Слои", list(LAYER_LABELS), default=list(LAYER_LABELS),
                                format_func=lambda k: LAYER_LABELS[k])
        focus = st.selectbox("Поток от объекта", ["(вся схема)", *_object_names(model)])
        direction = st.radio("Направление", ["both", "down", "up"], horizontal=True,
                             format_func={"both": "оба", "down": "откуда (вниз)", "up": "куда (вверх)"}.get)
        st.divider()
        trs = sorted(model.transformations.values(), key=lambda t: (t.source.name, t.target.name))
        choices = _object_names(model) + [f"TR: {t.source.name} → {t.target.name}" for t in trs]
        selected = st.selectbox("Карточка", choices, index=0, key="card")

    highlight = None
    lineage_key = st.session_state.get("lineage")
    if lineage_key:
        try:
            highlight = lineage_highlight(lineage(model, *lineage_key))
        except LineageError:
            highlight = None
    with left:
        dot = flow_dot(model, report, set(types), set(layers), None if focus.startswith("(") else focus,
                       direction, highlight, None if selected.startswith("TR: ") else selected)
        st.graphviz_chart(dot, width="stretch")
        legend = " ".join(f"<span style='border:2px solid {c};padding:0 6px;border-radius:4px'>уровень {lvl}</span>"
                          for lvl, c in LEVEL_COLOR.items())
        st.markdown(f"<small>Рамка — уровень автоматизации: {legend}; красное ребро — трансформация с рутиной; "
                    "оранжевым подсвечен путь lineage.</small>", unsafe_allow_html=True)

    st.divider()
    if selected.startswith("TR: "):
        t = trs[choices.index(selected) - len(_object_names(model))]
        _transformation_card(model, report, t)
    elif rules is not None:
        _object_card(model, report, rules, selected)


def _transformation_card(model: Model, report: Report, t) -> None:
    st.subheader(cards.transformation_title(model, t))
    a = cards.assessment_of(report, t.id)
    if a:
        st.markdown(f"Уровень автоматизации **{a[0]}**: {a[1]}")
    dtp = next((d for d in model.dtps.values() if d.source.key == t.source.key and d.target.key == t.target.key), None)
    if dtp:
        st.caption(f"DTP {dtp.name}: {'дельта' if dtp.mode == 'DELTA' else 'полная загрузка'}")
    st.dataframe(cards.transformation_rules(model, t), hide_index=True, width="stretch")
    for r in t.all_routines:
        with st.expander(f"Рутина ABAP ({r.kind.value}, {r.code_id})", expanded=True):
            st.code(r.code or "Кода нет в выгрузке RSAABAP", language="abap" if r.code else None)


def _object_card(model: Model, report: Report, rules: TypeRules, name: str) -> None:
    obj = model.find(name)
    st.subheader(name)
    a = cards.assessment_of(report, obj.ref.key if isinstance(obj, DataSource) else name)
    if a:
        st.markdown(f"Уровень автоматизации **{a[0]}**: {a[1]}" + (f", {a[2]:g} ч" if a[2] else ""))
    if not isinstance(obj, Adso | DataSource):
        st.info(getattr(obj, "info", "") or "Поля этого объекта в прототипе не разбираются.")
        return
    if obj.description:
        st.caption(obj.description)
    st.markdown("**Таблицы в ADB**")
    tables = cards.object_tables(model, rules, obj)
    st.dataframe(tables, hide_index=True, width="stretch")
    with st.expander("Данные из базы (первые 100 строк)"):
        _preview(list(tables["Таблица ADB"]), name)
    st.markdown("**Поля**")
    fields = cards.object_fields(model, rules, obj)
    st.dataframe(fields, hide_index=True, width="stretch")

    st.markdown("**Lineage поля**")
    c1, c2, c3 = st.columns([2, 1, 1])
    field_name = c1.selectbox("Поле", list(fields["Поле"]), key=f"lf_{name}", label_visibility="collapsed")
    if c2.button("Откуда пришло", key=f"down_{name}"):
        st.session_state["lineage"] = (name, field_name, DOWN)
        st.rerun()
    if c3.button("Куда идёт", key=f"up_{name}"):
        st.session_state["lineage"] = (name, field_name, UP)
        st.rerun()
    current = st.session_state.get("lineage")
    if current:
        try:
            tree = lineage(model, *current)
        except LineageError as e:
            st.warning(str(e))
            return
        title = "Откуда пришло" if current[2] == DOWN else "Куда идёт"
        st.markdown(f"{title}: **{current[0]}.{current[1]}** (путь подсвечен на схеме)")
        st.code(render(tree, current[2]), language=None)
        st.dataframe(cards.lineage_steps(tree, current[2]), hide_index=True, width="stretch")
        c1, c2 = st.columns([1, 3])
        c1.download_button("Скачать lineage (Excel)", cards.lineage_excel(tree, current[2]),
                           file_name=f"lineage_{current[0]}_{current[1]}.xlsx".replace("/", "_"))
        if c2.button("Сбросить подсветку"):
            st.session_state.pop("lineage", None)
            st.rerun()


def _preview(tables: list[str], name: str) -> None:
    """Превью данных развёрнутого объекта (ФП-4.5)."""
    c1, c2 = st.columns([3, 1])
    table = c1.selectbox("Таблица", tables, key=f"pv_{name}", label_visibility="collapsed")
    if not c2.button("Показать", key=f"pvb_{name}"):
        return
    try:
        with _connect() as conn:
            if not conn.execute("SELECT to_regclass(%s) IS NOT NULL", (table,)).fetchone()[0]:
                st.info(f"Таблицы {table} в базе нет: объект ещё не развёрнут.")
                return
            cur = conn.execute(f"SELECT * FROM {table} LIMIT 100")
            df = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description])
    except DbError as e:
        st.error(str(e))
        return
    st.caption(f"{table}: {len(df)} строк" + (" (показаны первые 100)" if len(df) == 100 else ""))
    st.dataframe(df, hide_index=True, width="stretch")


def tab_complexity(project: Project, model: Model, report: Report) -> None:
    st.subheader("Сложность переноса")
    c1, c2, c3 = st.columns(3)
    c1.metric("Объектов уровня 1", f"{report.by_level[1]} из {report.total}", f"{report.level1_share:.0%}",
              delta_color="off")
    c2.metric("Уровень 2 / уровень 3", f"{report.by_level[2]} / {report.by_level[3]}")
    c3.metric("Ручная работа по нормам", f"{report.hours:g} ч")
    counts: dict[str, list[int]] = {}
    for a in report.objects + report.unparsed:
        counts.setdefault(a.type, [0, 0, 0])[a.level - 1] += 1
    order = [t for t in TYPE_ORDER if t in counts] + sorted(set(counts) - set(TYPE_ORDER))
    df = pd.DataFrame([{"Тип": t, "Уровень 1": counts[t][0], "Уровень 2": counts[t][1], "Уровень 3": counts[t][2]}
                       for t in order])
    left, right = st.columns([1, 1])
    left.dataframe(df, hide_index=True, width="stretch")
    long = df.melt("Тип", var_name="Уровень", value_name="Объектов")
    levels = ["Уровень 1", "Уровень 2", "Уровень 3"]
    chart = alt.Chart(long[long["Объектов"] > 0]).mark_bar().encode(
        y=alt.Y("Тип:N", sort=order, title=None, axis=alt.Axis(labelLimit=220)),
        x=alt.X("sum(Объектов):Q", title="Объектов", axis=alt.Axis(tickMinStep=1)),
        color=alt.Color("Уровень:N", scale=alt.Scale(domain=levels, range=[LEVEL_COLOR[i] for i in (1, 2, 3)]),
                        legend=alt.Legend(orient="bottom", title=None)),
        order=alt.Order("Уровень:N"),
        tooltip=["Тип", "Уровень", "Объектов"])
    right.altair_chart(chart, width="stretch")
    st.markdown("**Объекты**")
    st.dataframe(pd.DataFrame([{"Тип": a.type, "Объект": a.name, "Описание": a.description, "Уровень": a.level,
                                "Причина": a.reason, "Часов": f"{a.hours:g}" if a.hours else ""}
                               for a in report.objects]),
                 hide_index=True, width="stretch")
    st.markdown("**Рутины ABAP**")
    if not report.routines:
        st.caption("Рутин нет")
    for r in report.routines:
        with st.expander(f"{r.kind}: {r.source} → {r.target}, {r.lines} строк"):
            st.code(r.code or "Кода нет в выгрузке", language="abap" if r.code else None)
    if report.unparsed:
        st.markdown("**Не разобрано**")
        st.dataframe(pd.DataFrame([{"Объект": a.name, "Где": a.description, "Причина": a.reason}
                                   for a in report.unparsed]), hide_index=True, width="stretch")
    if st.button("Сформировать Excel"):
        rules = _rules(project)
        package = generate(model, rules) if rules else None
        write_complexity(model, report, project.out_dir / "complexity.xlsx",
                         package.sql_lines if package else None, package.db_objects if package else None)
    _download("Скачать отчёт о сложности (Excel)", project.out_dir / "complexity.xlsx", "dl_complexity")


def tab_sql(project: Project, model: Model) -> None:
    st.subheader("Пакет SQL")
    rules = _rules(project)
    if rules is None:
        return
    c1, c2 = st.columns(2)
    dialect = c1.selectbox("Диалект", DIALECTS, format_func={"greenplum": "Greengage / Greenplum",
                                                             "postgres": "PostgreSQL"}.get)
    only_scope = c2.checkbox("Только состав переноса", value=False, disabled=not project.config.scope_from)
    try:
        package = generate(model, rules, dialect, project.config.scope_from if only_scope else None)
    except (RulesError, ScopeError) as e:
        st.error(str(e))
        return
    counts = package.counts()
    st.caption(f"Схем {counts.get('schema', 0)}, таблиц {counts.get('table', 0)}, функций {counts.get('function', 0)}, "
               f"строк SQL {package.sql_lines}")
    for o in package.stubs:
        st.warning(f"Заготовка, требует ручной доработки: {o.bw_object} ({'; '.join(o.stub)})")
    options = ["Весь пакет"] + sorted({o.bw_object for o in package.objects if o.bw_object})
    choice = st.selectbox("Объект", options)
    if choice == "Весь пакет":
        group = st.radio("Файл", list(GROUPS), horizontal=True, format_func=lambda g: GROUPS[g][0])
        text = package.files()[GROUPS[group][0]]
    else:
        text = "\n".join(o.sql for o in package.objects if o.bw_object == choice)
    st.code(text, language="sql", line_numbers=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in package.files().items():
            z.writestr(name, content)
    st.download_button("Скачать пакет SQL (zip)", buf.getvalue(), file_name="sql.zip", mime="application/zip")


def tab_deploy(project: Project, model: Model) -> None:
    st.subheader("Развёртывание, загрузка, прогон")
    rules = _rules(project)
    if rules is None:
        return
    try:
        conn = _connect()
    except DbError as e:
        st.error(str(e))
        return
    with conn:
        package = generate(model, rules, server_dialect(conn))
        st.markdown("**1. Развёртывание**")
        recreate = st.checkbox("Пересоздать таблицы (тестовые данные удалятся)")
        plan = make_plan(conn, package, recreate)
        counts = plan.counts()
        st.caption("План: " + ", ".join(f"{ACTION_TEXT[a]} {n}" for a, n in sorted(counts.items())))
        if plan.blocked:
            st.warning("Модель или правила изменились с последнего развёртывания, а таблицы уже есть. "
                       "Отметьте пересоздание.")
        with st.expander("Список объектов"):
            st.dataframe(pd.DataFrame([{"Объект": i.obj.name, "Вид": i.obj.kind, "Действие": ACTION_TEXT[i.action]}
                                       for i in plan.items]), hide_index=True, width="stretch")
        confirm = st.checkbox("Подтверждаю пересоздание", disabled=not recreate) if recreate else True
        if st.button("Развернуть", type="primary", disabled=not confirm):
            try:
                result = deploy(conn, package, recreate, out_dir=project.out_dir)
                (st.success if result.ok else st.error)(
                    f"Развёрнуто, номер {result.deploy_id}" if result.ok else result.error)
            except DeployError as e:
                st.error(str(e))

        st.markdown("**2. Загрузка CSV**")
        data = project.root / project.config.data
        md, loads, _ = data_files(data) if data.is_dir() else ([], [], [])
        files = [(o, p) for o, p in md + loads]
        c1, c2 = st.columns([2, 1])
        names = [f"{p.name} → {o}" for o, p in files]
        pick = c1.selectbox("Файл из папки данных", names) if names else None
        uploaded = c1.file_uploader("или свой файл", type=["csv", "txt"])
        target = c2.text_input("Объект (DataSource или признак)",
                               value=files[names.index(pick)][0] if pick else "")
        force = c2.checkbox("Загрузить повторно (--force)")
        if st.button("Загрузить"):
            path = files[names.index(pick)][1] if pick and uploaded is None else None
            if uploaded is not None:
                path = Path(tempfile.mkdtemp()) / uploaded.name
                path.write_bytes(uploaded.getvalue())
            try:
                res = load_file(conn, model, rules, target, path, force)
                st.success(f"Загружено строк {res.rows} в {res.table}, load_id {res.load_id}")
            except LoadError as e:
                st.error(str(e))
                if e.errors:
                    st.dataframe(pd.DataFrame([{"Строка": x.line, "Поле": x.field, "Значение": x.value,
                                                "Проблема": x.problem} for x in e.errors]), hide_index=True)

        st.markdown("**3. Прогон**")
        c1, c2 = st.columns([1, 3])
        load_id = c1.number_input("load_id", min_value=1, step=1)
        if c2.button("Прогон"):
            steps = []
            try:
                run_load(conn, model, package, int(load_id), project.config.scope_from,
                         progress=lambda s: steps.append(f"{s.title}: {s.rows} строк"))
                st.success(f"Загрузка {int(load_id)} активирована\n\n" + "\n\n".join(steps))
            except (RunError, ScopeError) as e:
                st.error(str(e))

        st.markdown("**Журнал загрузок**")
        meta = package.meta_schema
        try:
            rows = conn.execute(f"SELECT load_id, object_name, file_name, rows_loaded, status, loaded_at, "
                                f"activated_at, message FROM {meta}.load_log ORDER BY load_id DESC LIMIT 50").fetchall()
            conn.rollback()
            st.dataframe(pd.DataFrame(rows, columns=["load_id", "Объект", "Файл", "Строк", "Статус", "Загружено",
                                                     "Активировано", "Сообщение"]),
                         hide_index=True, width="stretch")
        except Exception:  # noqa: BLE001 — до первого развёртывания журнала нет
            conn.rollback()
            st.caption("Журнал появится после развёртывания.")

        st.divider()
        st.markdown("**Демо целиком** (импорт → отчёт → SQL → развёртывание с пересозданием → загрузки → прогон → "
                    "сверка)")
        if st.button("Запустить демо целиком"):
            lines: list[str] = []
            box = st.empty()

            def echo(text: str) -> None:
                lines.append(text)
                box.code("\n".join(lines), language=None)

            try:
                s = run_demo(project, conn, echo, server_dialect(conn))
                st.cache_data.clear()
                r = s.report
                checks = "; ".join(f"{x.object_name}: совпало {x.count(MATCH)} из {len(x.detail)}"
                                   for x in s.reconciles)
                st.success(f"Демо прошло за {s.seconds:.1f} с: уровень 1 — {r.by_level[1]} из {r.total} "
                           f"({r.level1_share:.0%}), строк SQL {s.package.sql_lines}, объектов БД "
                           f"{s.package.db_objects}; {checks}")
            except (ScenarioError, LoadError, RunError, ReconcileError, RulesError, ScopeError, DeployError) as e:
                st.error(f"Сценарий остановлен: {e}")


def tab_reconcile(project: Project, model: Model) -> None:
    st.subheader("Сверка с эталоном")
    data = project.root / project.config.data
    etalons = sorted(data.glob("etalon_*.csv")) if data.is_dir() else []
    c1, c2, c3 = st.columns([1, 2, 1])
    obj = c1.selectbox("Объект", sorted(model.adsos), index=sorted(model.adsos).index("ZSD_A02")
                       if "ZSD_A02" in model.adsos else 0)
    names = [p.name for p in etalons]
    default = names.index(f"etalon_{obj}.csv") if f"etalon_{obj}.csv" in names else 0
    pick = c2.selectbox("Эталон", names, index=default) if names else None
    uploaded = c2.file_uploader("или свой эталон", type=["csv"])
    tolerance = c3.number_input("Допуск", value=0.01, min_value=0.0, step=0.01, format="%.2f")
    if st.button("Сверить", type="primary"):
        path = data / pick if pick and uploaded is None else None
        if uploaded is not None:
            path = Path(tempfile.mkdtemp()) / uploaded.name
            path.write_bytes(uploaded.getvalue())
        if path is None:
            st.warning("Выберите эталон")
            return
        try:
            with _connect() as conn:
                st.session_state["reconcile"] = reconcile(conn, model, obj, path, project.out_dir, project.root,
                                                          tolerance)
        except (DbError, ReconcileError) as e:
            st.error(str(e))
            return
    r = st.session_state.get("reconcile")
    if r is None:
        return
    st.markdown(f"**{r.object_name}** ↔ `{r.etalon_file}`")
    cols = st.columns(len(STATUSES))
    for c, s in zip(cols, STATUSES, strict=True):
        c.metric(s, r.count(s))
    (st.success if r.ok else st.error)("Все строки совпали" if r.ok else f"Отличий: {len(r.problems)}")
    for lower in r.lower:
        st.info(f"Нижний слой {lower.object_name}: " + ("совпал" if lower.ok else "тоже не совпал"))
    st.markdown("**Суммы по показателям**")
    st.dataframe(r.totals.rename(columns={"measure": "Показатель", "unit": "Единица", "bw": "Эталон", "adb": "ADB",
                                          "diff": "Разница"}), hide_index=True, width="stretch")
    if len(r.problems):
        st.markdown("**Расхождения**")
        st.dataframe(r.problems, hide_index=True, width="stretch")
    if r.excel:
        _download("Скачать протокол (Excel)", r.excel, "dl_reconcile")


def main() -> None:
    st.set_page_config(page_title="DB-transfer", page_icon="🗄️", layout="wide")
    project = sidebar()
    if project is None:
        return
    tabs = st.tabs(["Импорт", "Схема", "Сложность", "SQL", "Развёртывание", "Сверка"])
    with tabs[0]:
        tab_import(project)
    model = _model(project)
    for tab, render_tab in zip(tabs[1:], (tab_schema, tab_complexity, tab_sql, tab_deploy, tab_reconcile),
                               strict=True):
        with tab:
            if not _need_model(model):
                continue
            if render_tab in (tab_schema, tab_complexity):
                render_tab(project, model, _report_of(project))
            else:
                render_tab(project, model)


main()
