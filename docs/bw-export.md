# Как выгрузить метаданные SAP BW через SE16

Прототип читает метаданные хранилища из CSV-выгрузок таблиц BW: одна таблица — один файл,
имя файла равно имени таблицы (`RSDIOBJ.csv` или `RSDIOBJ.txt`), все файлы в одной папке.
Тот же список печатает команда `dbtransfer tables`.

## Порядок выгрузки

1. Транзакция **SE16** (или SE16N), введите имя таблицы из списка ниже.
2. Включите технические имена полей: *Настройки → Параметры пользователя → «Имя поля»*.
   Первая строка файла должна содержать технические имена (`IOBJNM`, `OBJVERS`…), а не подписи.
3. Поле *Макс. число совпадений* оставьте пустым, чтобы выгрузить все строки.
   Для `DD03L` ограничьте `TABNAME` таблицами ADSO и справочников: `/BIC/A*`, `/BI0/P*`, `/BI0/T*`,
   `/BIC/P*`, `/BIC/T*`. Остальные таблицы выгружайте целиком: неактивные версии импорт отбросит сам.
4. Выполните (F8), затем *Система → Список → Сохранить → Локальный файл*, формат «Электронная таблица» или «Текст с табуляцией».
5. Сохраните файл под именем таблицы в общую папку выгрузки.

Кодировка (UTF-8, UTF-8 с BOM, Windows-1251) и разделитель (`;`, `,`, табуляция) определяются
автоматически; при необходимости их можно задать: `dbtransfer import --encoding cp1251 --delimiter tab`.
Значения, дополненные пробелами справа до длины поля, допустимы.

## Что делать после выгрузки

```
dbtransfer import <папка с выгрузкой> -p <проект>
```

Импорт не останавливается на ошибках в отдельных файлах. Всё, что не удалось разобрать
(нет файла, нет колонки, строка с неверным числом значений), перечислено в
`out/import_check.xlsx` с файлом, строкой и советом.

## Таблицы

Колонки из столбца «Обязательные колонки» должны быть в файле (значение может быть пустым,
например флаг `KEYFLAG`). Остальные колонки желательны. Точный состав задан в
`dbtransfer/rules/bw_tables.yaml`; если реальная система выгружает поля иначе, правится этот файл,
а не код. Таблицы с пометкой «гипотеза» собраны по документации и первыми проверяются на
настоящей выгрузке.

| Группа | Таблица | Обязательная | Обязательные колонки | Прочие колонки | Назначение |
|---|---|---|---|---|---|
| Поля таблиц | DD03L | да | TABNAME, FIELDNAME, AS4LOCAL, POSITION, KEYFLAG, ROLLNAME, DATATYPE, LENG, DECIMALS | AS4VERS, CHECKTABLE, INTTYPE, INTLEN, REFTABLE, REFFIELD | Поля, типы и ключи сгенерированных таблиц BW (в том числе активных таблиц ADSO /BIC/A<имя>2) |
| InfoObject | RSDIOBJ | да | IOBJNM, OBJVERS, IOBJTP | OBJSTAT | Список InfoObject и их вид |
| InfoObject | RSDIOBJT | нет | LANGU, IOBJNM, OBJVERS, TXTLG | TXTSH | Тексты InfoObject |
| InfoObject | RSDCHABAS | да | CHABASNM, OBJVERS, DATATP, INTLEN, ATTRIBFL, TXTTABFL | OUTPUTLEN, CONVEXIT, LOWERCASE, HIETABFL | Тип и свойства признака |
| InfoObject | RSDKYF | да | KYFNM, OBJVERS, KYFTP, DATATP, AGGREGATE | UNINM, FIXCUKY, FIXUNIT | Тип показателя, агрегация, единица или валюта |
| InfoObject | RSDBCHATR | нет | CHABASNM, ATTRINM, OBJVERS | POSIT, TIMDEPFL | Атрибуты признака |
| ADSO | RSOADSO | да | ADSONM, OBJVERS, ACTIVATE, CHANGELOG, ALLCHARKEY, DIRECTUPDATE | OBJSTAT, INFOAREA | Список ADSO и вид (по флагам) (состав полей — гипотеза) |
| ADSO | RSOADSOT | нет | LANGU, ADSONM, OBJVERS, TXTLG | — | Тексты ADSO (состав полей — гипотеза) |
| DataSource | RSDS | да | DATASOURCE, LOGSYS, OBJVERS, SRCTYPE | OBJSTAT, DELTAPROCESS | DataSource, исходная система и тип источника |
| DataSource | RSDST | нет | LANGU, DATASOURCE, LOGSYS, OBJVERS, TXTLG | — | Тексты DataSource |
| DataSource | RSDSSEGFD | да | DATASOURCE, LOGSYS, OBJVERS, POSIT, FIELDNM, DATATYPE, LENG, DECIMALS | SEGID, IOBJNM, KEYFLAG | Поля DataSource |
| Трансформации | RSTRAN | да | TRANID, OBJVERS, SOURCETYPE, SOURCENAME, TARGETTYPE, TARGETNAME | OBJSTAT, SOURCESUBTYPE, TARGETSUBTYPE, STARTROUTINE, ENDROUTINE, EXPERT | Трансформации, стартовые и конечные рутины |
| Трансформации | RSTRANRULE | да | TRANID, OBJVERS, RULEID, RULETYPE | SEGID, PARAM, CODEID | Правила полей и их вид (состав полей — гипотеза) |
| Трансформации | RSTRANFIELD | да | TRANID, OBJVERS, RULEID, PARAMTYPE, FIELDNM | SEGID, PARAMNM | Поля источника (PARAMTYPE 0) и цели (PARAMTYPE 1) каждого правила (состав полей — гипотеза) |
| Трансформации | RSAABAP | нет | CODEID, OBJVERS, LINE_NO | LINE | Код ABAP-рутин (состав полей — гипотеза) |
| DTP | RSBKDTP | да | DTP, OBJVERS, SRCTP, SRC, TGTTP, TGT, UPDMODE | OBJSTAT | Загрузки между объектами и режим (FULL или DELTA) |
| CompositeProvider | RSOHCPR | нет | HCPRNM, OBJVERS, PARTPROV | — | CompositeProvider и участники (одна строка на участника), только для отчёта (состав полей — гипотеза) |
| Классические объекты BW | RSDODSO | нет | ODSOBJECT, OBJVERS | ODSOTYPE | Классические DSO, только для отчёта |
| Запросы | RSZCOMPDIR | нет | COMPID, OBJVERS, INFOCUBE | DEFTP | BEx-запросы и провайдер, на котором они построены, только для отчёта |
| Иерархии | RSHIEDIR | нет | HIEID, OBJVERS, IOBJNM | HIENM | Иерархии признаков, только для отчёта |
| Цепочки процессов | RSPCCHAIN | нет | CHAIN_ID, OBJVERS, TYPE, VARIANTE | — | Шаги цепочек процессов, только для отчёта |
