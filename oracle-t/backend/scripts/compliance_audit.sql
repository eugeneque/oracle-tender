-- Сверка матрицы соответствия: признаки ложных итогов по всем посчитанным закупкам
-- (15.10.2026, раздел 60 ARCHITECTURE.md). Только чтение. Запуск:
--   psql -h 127.0.0.1 -U oraclet oraclet --csv -f compliance_audit.sql > compliance_audit.csv
-- Одна строка — одна находка; колонка check — тип признака:
--   reference_fails      эталон ТЗ с итогом «не проходит»
--   reference_low        эталон ТЗ ниже 90%
--   all_fail             «не проходят» все производители закупки (обычно ошибка извлечения)
--   lot_conflict         «не соответствует» по значению параметра, другое значение которого
--                        тот же производитель выполнил (смешанный лот: 230 В и 400 В)
--   transformer_vs_meter требование к ТТ/ТН решено по характеристикам счётчика
--   rule_wording         правило «трёхпозиционное реле» на требовании не про реле
--   critical_weak_fail   «не соответствует» по критичному требованию с уверенностью < 0,75

WITH wp AS (
    SELECT w.*, t.external_id, left(t.title, 90) AS title, coalesce(m.brand_name, m.legal_name) AS maker
    FROM win_percentages w
    JOIN tenders t ON t.id = w.tender_id
    JOIN manufacturers m ON m.id = w.manufacturer_id
    WHERE w.is_current
),
cells AS (
    SELECT e.*, t.external_id, left(t.title, 90) AS title, coalesce(m.brand_name, m.legal_name) AS maker,
           r.criticality, coalesce(r.normalized_text, r.text) AS req
    FROM compliance_matrix_entries e
    JOIN requirements r ON r.id = e.requirement_id
    JOIN tenders t ON t.id = e.tender_id
    JOIN manufacturers m ON m.id = e.manufacturer_id
),
req AS (
    SELECT r.id, r.tender_id, r.normalized_text,
           lower(btrim(split_part(r.normalized_text, ':', 1))) AS subject,
           lower(btrim(substr(r.normalized_text, strpos(r.normalized_text, ':') + 1))) AS value
    FROM requirements r
    WHERE r.kind = 'product' AND strpos(r.normalized_text, ':') > 0
)
SELECT 'reference_fails' AS check, external_id, title, maker, percentage, verdict,
       NULL AS requirement, NULL AS status, NULL AS source, NULL::numeric AS confidence,
       left(reason_summary, 250) AS detail
FROM wp WHERE tz_reference AND verdict = 'fails'

UNION ALL
SELECT 'reference_low', external_id, title, maker, percentage, verdict, NULL, NULL, NULL, NULL,
       left(reason_summary, 250)
FROM wp WHERE tz_reference AND percentage < 90 AND verdict IS DISTINCT FROM 'fails'

UNION ALL
SELECT 'all_fail', min(external_id), min(title), count(*) || ' производителей', NULL, 'fails',
       NULL, NULL, NULL, NULL, left(min(reason_summary), 250)
FROM wp GROUP BY tender_id
HAVING count(*) >= 3 AND bool_and(verdict = 'fails')

UNION ALL
SELECT 'lot_conflict', c.external_id, c.title, c.maker, NULL, NULL,
       b.normalized_text, c.status, c.source, c.confidence,
       'выполнено: ' || a.normalized_text || ' | ' || left(coalesce(c.explanation, ''), 180)
FROM req a
JOIN req b ON b.tender_id = a.tender_id AND b.subject = a.subject AND b.id <> a.id
JOIN compliance_matrix_entries ca ON ca.requirement_id = a.id AND ca.status = 'meets'
JOIN cells c ON c.requirement_id = b.id AND c.manufacturer_id = ca.manufacturer_id
            AND c.status = 'not_meets' AND c.source NOT IN ('expert_rule', 'tz_reference')
WHERE a.subject NOT LIKE 'модель прибора%'
  AND (
        (a.value IN ('да', 'есть', 'имеется') AND b.value IN ('нет', 'отсутствует', 'не допускается'))
     OR (b.value IN ('да', 'есть', 'имеется') AND a.value IN ('нет', 'отсутствует', 'не допускается'))
     OR (a.value ~ '\d' AND b.value ~ '\d'
         AND regexp_replace(a.value, '[^0-9.,]', '', 'g') <> regexp_replace(b.value, '[^0-9.,]', '', 'g'))
  )

UNION ALL
SELECT 'transformer_vs_meter', external_id, title, maker, NULL, NULL, req, status, source, confidence,
       left(explanation, 250)
FROM cells
WHERE status <> 'no_data' AND source <> 'tz_reference'
  AND split_part(req, ':', 1) ~* '\mт[тн]\M|трансформатор\w*\s+(тока|напряжени)'
  AND split_part(req, ':', 1) !~* 'включени|подключ|через'
  AND coalesce(explanation, '') !~* '\mт[тн]\M|трансформатор'

UNION ALL
SELECT 'rule_wording', external_id, title, maker, NULL, NULL, req, status, source, confidence,
       left(explanation, 250)
FROM cells
WHERE source = 'expert_rule' AND explanation ILIKE '%трёхпозиционн%'
  AND req !~* 'тр[её]х\s*-?\s*позиционн|3\s*-?\s*х?\s*-?\s*позиционн'

UNION ALL
SELECT 'critical_weak_fail', external_id, title, maker, NULL, NULL, req, status, source, confidence,
       left(explanation, 250)
FROM cells
WHERE status = 'not_meets' AND criticality = 'critical' AND confidence < 0.75
  AND source NOT IN ('expert_rule', 'tz_reference', 'astra_catalog')

ORDER BY 1, 2, 4;
