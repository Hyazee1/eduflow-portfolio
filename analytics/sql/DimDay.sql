WITH v AS (
    SELECT week
    FROM public.schedule_versions
    WHERE id = '__VERSION_ID__'
)
SELECT (v.week + g.day_index)::date AS day,
       g.day_index AS weekday_index,
       CASE g.day_index
           WHEN 0 THEN '周一' WHEN 1 THEN '周二' WHEN 2 THEN '周三'
           WHEN 3 THEN '周四' WHEN 4 THEN '周五'
       END AS weekday_name
FROM v CROSS JOIN generate_series(0, 4) AS g(day_index)
ORDER BY weekday_index
