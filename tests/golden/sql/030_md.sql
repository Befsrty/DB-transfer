-- Справочники: атрибуты и тексты признаков
-- Сгенерировано DB-transfer 0.1.0 (dbtransfer generate), диалект greenplum.
-- Модель: Продажи (демо), хеш df4449f68e1ee540. Файл перезаписывается при генерации, правки вносите в модель и правила.

CREATE TABLE IF NOT EXISTS dbt_md.country_text (
    country varchar(3) NOT NULL,
    langu   char(1) NOT NULL,
    txtsh   varchar(20),
    txtmd   varchar(40),
    txtlg   varchar(60),
    PRIMARY KEY (country, langu)
)
DISTRIBUTED BY (country);
COMMENT ON TABLE dbt_md.country_text IS 'BW: тексты признака 0COUNTRY. Страна';
COMMENT ON COLUMN dbt_md.country_text.country IS 'BW: 0COUNTRY.0COUNTRY (CHAR 3). Страна';
COMMENT ON COLUMN dbt_md.country_text.langu IS 'Язык текста (R русский, E английский)';
COMMENT ON COLUMN dbt_md.country_text.txtsh IS 'Краткий текст';
COMMENT ON COLUMN dbt_md.country_text.txtmd IS 'Средний текст';
COMMENT ON COLUMN dbt_md.country_text.txtlg IS 'Длинный текст';

CREATE TABLE IF NOT EXISTS dbt_md.material_attr (
    material   varchar(40) NOT NULL,
    matl_group varchar(9),
    PRIMARY KEY (material)
)
DISTRIBUTED BY (material);
COMMENT ON TABLE dbt_md.material_attr IS 'BW: атрибуты признака 0MATERIAL. Материал';
COMMENT ON COLUMN dbt_md.material_attr.material IS 'BW: 0MATERIAL.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_md.material_attr.matl_group IS 'BW: атрибут 0MATL_GROUP признака 0MATERIAL. Группа материалов';

CREATE TABLE IF NOT EXISTS dbt_md.material_text (
    material varchar(40) NOT NULL,
    langu    char(1) NOT NULL,
    txtsh    varchar(20),
    txtmd    varchar(40),
    txtlg    varchar(60),
    PRIMARY KEY (material, langu)
)
DISTRIBUTED BY (material);
COMMENT ON TABLE dbt_md.material_text IS 'BW: тексты признака 0MATERIAL. Материал';
COMMENT ON COLUMN dbt_md.material_text.material IS 'BW: 0MATERIAL.0MATERIAL (CHAR 40). Материал';
COMMENT ON COLUMN dbt_md.material_text.langu IS 'Язык текста (R русский, E английский)';
COMMENT ON COLUMN dbt_md.material_text.txtsh IS 'Краткий текст';
COMMENT ON COLUMN dbt_md.material_text.txtmd IS 'Средний текст';
COMMENT ON COLUMN dbt_md.material_text.txtlg IS 'Длинный текст';

CREATE TABLE IF NOT EXISTS dbt_md.matl_group_text (
    matl_group varchar(9) NOT NULL,
    langu      char(1) NOT NULL,
    txtsh      varchar(20),
    txtmd      varchar(40),
    txtlg      varchar(60),
    PRIMARY KEY (matl_group, langu)
)
DISTRIBUTED BY (matl_group);
COMMENT ON TABLE dbt_md.matl_group_text IS 'BW: тексты признака 0MATL_GROUP. Группа материалов';
COMMENT ON COLUMN dbt_md.matl_group_text.matl_group IS 'BW: 0MATL_GROUP.0MATL_GROUP (CHAR 9). Группа материалов';
COMMENT ON COLUMN dbt_md.matl_group_text.langu IS 'Язык текста (R русский, E английский)';
COMMENT ON COLUMN dbt_md.matl_group_text.txtsh IS 'Краткий текст';
COMMENT ON COLUMN dbt_md.matl_group_text.txtmd IS 'Средний текст';
COMMENT ON COLUMN dbt_md.matl_group_text.txtlg IS 'Длинный текст';

CREATE TABLE IF NOT EXISTS dbt_md.sold_to_attr (
    sold_to varchar(10) NOT NULL,
    country varchar(3),
    PRIMARY KEY (sold_to)
)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_md.sold_to_attr IS 'BW: атрибуты признака 0SOLD_TO. Заказчик';
COMMENT ON COLUMN dbt_md.sold_to_attr.sold_to IS 'BW: 0SOLD_TO.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_md.sold_to_attr.country IS 'BW: атрибут 0COUNTRY признака 0SOLD_TO. Страна';

CREATE TABLE IF NOT EXISTS dbt_md.sold_to_text (
    sold_to varchar(10) NOT NULL,
    langu   char(1) NOT NULL,
    txtsh   varchar(20),
    txtmd   varchar(40),
    txtlg   varchar(60),
    PRIMARY KEY (sold_to, langu)
)
DISTRIBUTED BY (sold_to);
COMMENT ON TABLE dbt_md.sold_to_text IS 'BW: тексты признака 0SOLD_TO. Заказчик';
COMMENT ON COLUMN dbt_md.sold_to_text.sold_to IS 'BW: 0SOLD_TO.0SOLD_TO (CHAR 10). Заказчик';
COMMENT ON COLUMN dbt_md.sold_to_text.langu IS 'Язык текста (R русский, E английский)';
COMMENT ON COLUMN dbt_md.sold_to_text.txtsh IS 'Краткий текст';
COMMENT ON COLUMN dbt_md.sold_to_text.txtmd IS 'Средний текст';
COMMENT ON COLUMN dbt_md.sold_to_text.txtlg IS 'Длинный текст';
