"""Функции, которые есть в целевой базе. Вызов функции не из списка переводчик помечает как todo:
значит, sqlglot оставил функцию источника как есть (DECODE со сложной логикой, SYS_CONTEXT, NEWID …).

Список PostgreSQL — функции, которые есть и в PostgreSQL 9.4 (Greenplum 6), и в 12 (Greenplum 7, ADB 7.5);
список ClickHouse — частые функции, без учёта регистра.
"""

from __future__ import annotations

POSTGRES = frozenset("""
abs acos asin atan atan2 avg cast bit_and bit_or bool_and bool_or btrim cbrt ceil ceiling char_length
character_length chr coalesce concat concat_ws corr cos cot count covar_pop covar_samp cume_dist
current_setting date_part date_trunc decode degrees dense_rank div encode every exp extract first_value
floor format generate_series greatest initcap isfinite justify_days justify_hours justify_interval lag
last_value lead least left length ln log lower lpad ltrim make_date make_interval make_time make_timestamp
max md5 min mod nextval now nth_value ntile nullif octet_length overlay percent_rank percentile_cont
percentile_disc pi position power quote_ident quote_literal radians random rank regexp_matches
regexp_replace regexp_split_to_array regexp_split_to_table repeat replace reverse right round row_number
rpad rtrim sign sin split_part sqrt stddev stddev_pop stddev_samp string_agg strpos substr substring sum
tan timeofday to_char to_date to_hex to_number to_timestamp translate trim trunc unnest upper var_pop
var_samp variance width_bucket array_agg array_length array_to_string array_upper array_lower
age clock_timestamp statement_timestamp transaction_timestamp json_agg json_build_object to_json
row_to_json jsonb_agg jsonb_build_object generate_subscripts num_nonnulls num_nulls ascii bit_length
regr_slope regr_intercept regr_count regr_r2 regr_avgx regr_avgy regr_sxx regr_syy regr_sxy
mode grouping
""".split())

CLICKHOUSE = frozenset(name.lower() for name in """
abs acos asin atan avg ceil ceiling coalesce concat concat_ws cos count countIf sumIf avgIf exp floor
greatest least length lower upper lowerUTF8 upperUTF8 ltrim rtrim trim trimBoth trimLeft trimRight max min
mod nullIf ifNull isNull isNotNull position positionUTF8 locate pow power replace replaceAll replaceOne
replaceRegexpAll replaceRegexpOne reverse round roundBankers sign sin sqrt substring substr substringUTF8
sum tan toDate toDate32 toDateTime toDateTime64 toString toInt8 toInt16 toInt32 toInt64 toUInt8 toUInt32
toUInt64 toFloat32 toFloat64 toDecimal32 toDecimal64 toDecimal128 toDecimal256 toStartOfDay
toStartOfMonth toStartOfYear toStartOfQuarter toStartOfWeek toMonday toYear toMonth toDayOfMonth
toDayOfWeek toDayOfYear toHour toMinute toSecond toQuarter toLastDayOfMonth toYYYYMM toYYYYMMDD
formatDateTime parseDateTimeBestEffort parseDateTime parseDateTimeOrNull dateDiff date_diff dateAdd
date_add dateSub date_sub addDays addMonths addYears addHours addMinutes addSeconds subtractDays
subtractMonths subtractYears toIntervalDay toIntervalMonth toIntervalYear toIntervalSecond
toIntervalHour toIntervalMinute now today yesterday currentDatabase currentUser if multiIf
row_number rank dense_rank lag lead lagInFrame leadInFrame first_value last_value nth_value ntile
groupArray groupConcat arrayStringConcat arrayJoin splitByChar splitByString leftPad rightPad lpad rpad
left right leftUTF8 rightUTF8 char_length character_length lengthUTF8 extract dateTrunc date_trunc
toStartOfInterval stddevPop stddevSamp varPop varSamp corr covarPop covarSamp quantile median uniq
uniqExact any anyLast argMin argMax CAST accurateCastOrNull toTypeName match like ilike notLike
multiSearchAny countDistinct STRING_AGG percent_rank cume_dist exp2 log log2 log10 ln e pi
""".split())


def is_known_function(name: str, target: str) -> bool:
    known = CLICKHOUSE if target == "clickhouse" else POSTGRES
    return name.lower() in known
