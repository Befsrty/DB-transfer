-- Справочники: атрибуты и тексты признаков
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект clickhouse.
-- Модель: Продажи (демо), хеш 66f9b522a2a87a8a. Файл перезаписывается при генерации, правки вносите в модель и правила.

CREATE TABLE IF NOT EXISTS dbt_md.country_text
(
    country String COMMENT 'BW: 0COUNTRY.0COUNTRY (CHAR 3). Страна',
    langu   String COMMENT 'Язык текста (R русский, E английский)',
    txtsh   Nullable(String) COMMENT 'Краткий текст',
    txtmd   Nullable(String) COMMENT 'Средний текст',
    txtlg   Nullable(String) COMMENT 'Длинный текст'
)
ENGINE = ReplacingMergeTree
ORDER BY (country, langu)
COMMENT 'BW: тексты признака 0COUNTRY. Страна';

CREATE TABLE IF NOT EXISTS dbt_md.material_attr
(
    material   String COMMENT 'BW: 0MATERIAL.0MATERIAL (CHAR 40). Материал',
    matl_group Nullable(String) COMMENT 'BW: атрибут 0MATL_GROUP признака 0MATERIAL. Группа материалов'
)
ENGINE = ReplacingMergeTree
ORDER BY material
COMMENT 'BW: атрибуты признака 0MATERIAL. Материал';

CREATE TABLE IF NOT EXISTS dbt_md.material_text
(
    material String COMMENT 'BW: 0MATERIAL.0MATERIAL (CHAR 40). Материал',
    langu    String COMMENT 'Язык текста (R русский, E английский)',
    txtsh    Nullable(String) COMMENT 'Краткий текст',
    txtmd    Nullable(String) COMMENT 'Средний текст',
    txtlg    Nullable(String) COMMENT 'Длинный текст'
)
ENGINE = ReplacingMergeTree
ORDER BY (material, langu)
COMMENT 'BW: тексты признака 0MATERIAL. Материал';

CREATE TABLE IF NOT EXISTS dbt_md.matl_group_text
(
    matl_group String COMMENT 'BW: 0MATL_GROUP.0MATL_GROUP (CHAR 9). Группа материалов',
    langu      String COMMENT 'Язык текста (R русский, E английский)',
    txtsh      Nullable(String) COMMENT 'Краткий текст',
    txtmd      Nullable(String) COMMENT 'Средний текст',
    txtlg      Nullable(String) COMMENT 'Длинный текст'
)
ENGINE = ReplacingMergeTree
ORDER BY (matl_group, langu)
COMMENT 'BW: тексты признака 0MATL_GROUP. Группа материалов';

CREATE TABLE IF NOT EXISTS dbt_md.sold_to_attr
(
    sold_to String COMMENT 'BW: 0SOLD_TO.0SOLD_TO (CHAR 10). Заказчик',
    country Nullable(String) COMMENT 'BW: атрибут 0COUNTRY признака 0SOLD_TO. Страна'
)
ENGINE = ReplacingMergeTree
ORDER BY sold_to
COMMENT 'BW: атрибуты признака 0SOLD_TO. Заказчик';

CREATE TABLE IF NOT EXISTS dbt_md.sold_to_text
(
    sold_to String COMMENT 'BW: 0SOLD_TO.0SOLD_TO (CHAR 10). Заказчик',
    langu   String COMMENT 'Язык текста (R русский, E английский)',
    txtsh   Nullable(String) COMMENT 'Краткий текст',
    txtmd   Nullable(String) COMMENT 'Средний текст',
    txtlg   Nullable(String) COMMENT 'Длинный текст'
)
ENGINE = ReplacingMergeTree
ORDER BY (sold_to, langu)
COMMENT 'BW: тексты признака 0SOLD_TO. Заказчик';
