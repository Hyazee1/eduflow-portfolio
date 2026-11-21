WITH v AS (
    SELECT * FROM public.schedule_versions WHERE id='__VERSION_ID__'
), sessions AS (
    SELECT s.* FROM public.course_sessions s JOIN v ON v.id=s.version_id
    WHERE s.status NOT IN ('CANCELLED','SUSPENDED')
), confirmations AS (
    SELECT c.*,s.fingerprint AS current_fingerprint
    FROM public.schedule_confirmations c JOIN sessions s ON s.id=c.session_id
)
SELECT v.id AS version_id,v.week,v.status AS version_status,
       CASE v.status WHEN 'ACTIVE' THEN '已生效' WHEN 'PUBLISHED_PENDING_CONFIRMATION' THEN '待确认'
            WHEN 'CANCELLED' THEN '已取消' ELSE v.status END AS version_status_name,
       v.published_at AT TIME ZONE 'Asia/Shanghai' AS published_at,
       v.confirmation_deadline AT TIME ZONE 'Asia/Shanghai' AS confirmation_deadline,
       v.activated_at AT TIME ZONE 'Asia/Shanghai' AS activated_at,
       v.solver_status,(v.report->>'wall_seconds')::double precision AS solve_seconds,
       (v.report->>'hard_conflicts')::integer AS hard_conflicts,
       (v.report->'optimization'->'baseline'->>1)::integer AS baseline_first_count,
       (v.report->'optimization'->'baseline'->>2)::integer AS baseline_any_count,
       (v.report->'optimization'->>'improved')::boolean AS preference_improved,
       (SELECT COUNT(*) FROM confirmations WHERE fingerprint<>current_fingerprint) AS stale_confirmations,
       CASE WHEN NOT EXISTS(SELECT 1 FROM confirmations WHERE reason NOT LIKE 'DEMO_SIMULATION:%')
            AND EXISTS(SELECT 1 FROM confirmations) THEN '模拟账号确认' ELSE '本人或混合确认' END AS confirmation_mode,
       CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai' AS extracted_at
FROM v
