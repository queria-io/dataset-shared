{# 5 桁の市区町村コード（検査数字なし）に検査数字を付けて、6 桁の全国地方公共団体コードにする。
   検査数字は各桁に 6・5・4・3・2 を掛けた和を 11 で割った余り r から (11 - r) の一の位
   （r = 0 なら 1、r = 1 なら 0）。総務省「全国地方公共団体コード」の定め。
   5 桁の数字でない値は NULL を返す。 #}

{% macro local_government_code(city_code) -%}
case when regexp_full_match({{ city_code }}, '[0-9]{5}') then
    {{ city_code }} || cast((11 - (
        6 * cast(substr({{ city_code }}, 1, 1) as integer)
        + 5 * cast(substr({{ city_code }}, 2, 1) as integer)
        + 4 * cast(substr({{ city_code }}, 3, 1) as integer)
        + 3 * cast(substr({{ city_code }}, 4, 1) as integer)
        + 2 * cast(substr({{ city_code }}, 5, 1) as integer)
    ) % 11) % 10 as varchar)
end
{%- endmacro %}
