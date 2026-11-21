WITH v AS (
    SELECT snapshot
    FROM public.schedule_versions
    WHERE id = '__VERSION_ID__'
)
SELECT j.value ->> 'code' AS slot_code,
       (j.value ->> 'ordinal')::integer AS slot_order,
       j.value ->> 'start' AS start_time,
       j.value ->> 'end' AS end_time,
       j.value ->> 'kind' AS kind,
       j.value ->> 'period' AS period,
       (EXTRACT(EPOCH FROM ((j.value ->> 'end')::time
           - (j.value ->> 'start')::time)) / 60)::integer AS duration_minutes,
       (j.value ->> 'code') || ' ' || (j.value ->> 'start')
           || '–' || (j.value ->> 'end') AS slot_label
FROM v CROSS JOIN LATERAL json_array_elements(v.snapshot -> 'slots') AS j(value)
ORDER BY slot_order
