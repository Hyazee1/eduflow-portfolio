WITH extracted AS (
-- One row per request in the selected week, including requests not scheduled.
-- Use EXISTS for one-to-many preferences instead of multiplying request rows.
WITH v AS (SELECT id, organization_id, week, status, snapshot FROM public.schedule_versions WHERE id = '__VERSION_ID__'),
scheduled AS (
    SELECT se.request_id, s.id AS session_id, s.teacher_id, s.room_id,
           s.day, s.slot_code, s.fingerprint
    FROM public.course_sessions s
    JOIN v ON v.id = s.version_id
    JOIN public.session_enrollments se ON se.session_id = s.id
    WHERE s.status NOT IN ('CANCELLED', 'SUSPENDED')
),
preferences AS (
    SELECT p.request_id, p.day_index, p.slot_code, p.rank,
           MIN(p.rank) OVER (PARTITION BY p.request_id) AS best_rank
    FROM public.enrollment_time_preferences p
),
cohort AS (
    SELECT d.value ->> 'id' AS request_id
    FROM v CROSS JOIN LATERAL json_array_elements(v.snapshot -> 'demands') d
)
SELECT e.id AS request_id, v.id AS version_id, v.week,
       e.student_id, e.course_id, e.offering_id, e.status AS request_status,
       CASE WHEN e.status = 'APPROVED' THEN 1 ELSE 0 END AS is_approved,
       CASE WHEN s.session_id IS NOT NULL THEN 1 ELSE 0 END AS is_scheduled,
       CASE WHEN EXISTS (SELECT 1 FROM cohort c WHERE c.request_id=e.id)
            THEN 1 ELSE 0 END AS in_solver_cohort,
       s.session_id, s.teacher_id, s.room_id, s.day AS scheduled_day, s.slot_code,
       CASE WHEN EXISTS (
           SELECT 1 FROM preferences p WHERE p.request_id = e.id
             AND p.day_index = (s.day - v.week) AND p.slot_code = s.slot_code
       ) THEN 1 ELSE 0 END AS in_any_preferred_slot,
       CASE WHEN EXISTS (
           SELECT 1 FROM preferences p WHERE p.request_id = e.id
             AND p.day_index = (s.day - v.week) AND p.slot_code = s.slot_code
             AND p.rank = p.best_rank
       ) THEN 1 ELSE 0 END AS in_first_preferred_slot,
       CASE WHEN sc.decision = 'ACCEPTED' AND tc.decision = 'ACCEPTED'
            THEN 1 ELSE 0 END AS both_confirmed,
       CASE WHEN v.status = 'ACTIVE' AND s.session_id IS NOT NULL
            THEN 1 ELSE 0 END AS is_effective,
       CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai' AS extracted_at
FROM public.enrollment_requests e
JOIN v ON v.organization_id = e.organization_id AND v.week = e.week
LEFT JOIN scheduled s ON s.request_id = e.id
LEFT JOIN public.schedule_confirmations sc
       ON sc.session_id = s.session_id AND sc.user_id = e.student_id
      AND sc.fingerprint = s.fingerprint
LEFT JOIN public.schedule_confirmations tc
       ON tc.session_id = s.session_id AND tc.user_id = s.teacher_id
      AND tc.fingerprint = s.fingerprint

) SELECT extracted.*, CASE WHEN in_first_preferred_slot=1 THEN '首选时段' WHEN in_any_preferred_slot=1 THEN '其他意向时段' ELSE '非意向时段' END AS preference_band FROM extracted