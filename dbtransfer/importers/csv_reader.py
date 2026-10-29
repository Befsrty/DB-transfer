"""Чтение одной CSV-выгрузки таблицы BW (SE16) с проверкой формата.

Кодировка определяется без внешних библиотек: сначала UTF-8 (с BOM или без),
при ошибке декодирования Windows-1251 (ТЗ, ФП-2.2). Разделитель — тот из «;», табуляции
и «,», которого больше всего в строке заголовка. Оба можно задать вручную.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from dbtransfer.config import BwTable
from dbtransfer.model import Issue, Severity

DELIMITERS = {";": "точка с запятой", "\t": "табуляция", ",": "запятая"}
EXTENSIONS = (".csv", ".txt")


@dataclass
class Row:
    line: int  # номер строки в файле, заголовок — строка 1
    values: dict[str, str]

    def __getitem__(self, column: str) -> str:
        return self.values.get(column, "")

    def get(self, column: str, default: str = "") -> str:
        return self.values.get(column, default) or default


@dataclass
class TableFile:
    table: str
    path: Path | None = None
    encoding: str = ""
    delimiter: str = ""
    usable: bool = False
    rows: list[Row] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def file_name(self) -> str:
        return self.path.name if self.path else f"{self.table}.csv"

    def issue(self, severity: Severity, message: str, line: int | None = None) -> None:
        self.issues.append(Issue(severity=severity, message=message, file=self.file_name, line=line))


def find_file(folder: Path, table: str) -> Path | None:
    """Файл таблицы в папке: имя без учёта регистра, расширение .csv или .txt."""
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.suffix.lower() in EXTENSIONS and path.stem.upper() == table.upper():
            return path
    return None


def decode(raw: bytes, encoding: str | None = None) -> tuple[str, str]:
    """Текст файла и название кодировки для отчёта."""
    if encoding:
        return raw.decode(encoding), encoding
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1251"), "Windows-1251"
    return text, "UTF-8 с BOM" if raw.startswith(b"\xef\xbb\xbf") else "UTF-8"


def detect_delimiter(header: str) -> str:
    counts = {d: header.count(d) for d in DELIMITERS}
    best = max(counts, key=lambda d: counts[d])
    return best if counts[best] else ";"


def read_table(folder: Path, spec: BwTable, encoding: str | None = None, delimiter: str | None = None) -> TableFile:
    result = TableFile(table=spec.name, path=find_file(folder, spec.name))
    if result.path is None:
        severity = Severity.ERROR if spec.required else Severity.WARNING
        tail = "" if spec.required else "; без неё часть сведений будет пропущена"
        result.issue(severity, f"Нет файла {spec.name}.csv. Выгрузите таблицу {spec.name} через SE16 "
                               f"({spec.purpose.lower()}){tail}")
        return result

    try:
        text, result.encoding = decode(result.path.read_bytes(), encoding)
    except (UnicodeDecodeError, LookupError) as e:
        result.issue(Severity.ERROR, f"Не удалось прочитать файл в кодировке {encoding}: {e}. "
                                     "Сохраните выгрузку в UTF-8 или Windows-1251")
        return result
    lines = text.splitlines()
    if not lines or not lines[0].strip():
        result.issue(Severity.ERROR, "Файл пустой: нет строки заголовка с техническими именами полей")
        return result
    result.delimiter = delimiter or detect_delimiter(lines[0])

    reader = csv.reader(io.StringIO(text), delimiter=result.delimiter)
    header = [h.strip().upper() for h in next(reader)]
    missing = [c for c in spec.required_columns if c not in header]
    if missing:
        result.issue(Severity.ERROR,
                     f"Нет обязательных колонок: {', '.join(missing)}. Таблица не использована. "
                     "Добавьте колонки в выгрузку SE16 (выбор полей, технические имена) "
                     "или поправьте dbtransfer/rules/bw_tables.yaml", line=1)
        return result
    extra = [c for c in header if c not in spec.columns]
    if extra:
        result.issue(Severity.INFO, f"Колонки не используются прототипом: {', '.join(extra)}", line=1)

    for values in reader:
        line = reader.line_num
        if not any(v.strip() for v in values):
            continue
        if len(values) != len(header):
            result.issue(Severity.WARNING,
                         f"{len(values)} значений вместо {len(header)}, строка пропущена. "
                         "Проверьте разделитель и кавычки в этой строке", line=line)
            continue
        # SE16 дополняет значения пробелами справа до длины поля; слева значения не трогаем
        result.rows.append(Row(line=line, values={h: v.rstrip() for h, v in zip(header, values, strict=True)}))
    result.usable = True
    return result
