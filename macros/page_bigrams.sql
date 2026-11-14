{#
  全文検索用の索引。ページの本文を空白・改行を除いて 2 文字ずつ区切り、ページごとの出現回数（tf）を数える。
  BM25 などの順位付けは検索する側で tf と各ページの文字数から計算する。

  {{ page_bigrams(ref('pages'), ['sha256', 'page']) }}
#}
{% macro page_bigrams(relation, keys, text_column='text') %}
{%- set key_list = keys | join(', ') -%}
WITH chars AS (
    SELECT {{ key_list }}, regexp_replace({{ text_column }}, '\s+', '', 'g') AS t
    FROM {{ relation }}
)

SELECT
    {{ key_list }},
    substr(t, i, 2) AS gram,
    count(*)::INTEGER AS tf
FROM chars, range(1, length(t)) AS r(i)
GROUP BY ALL
ORDER BY gram, {{ key_list }}
{% endmacro %}
