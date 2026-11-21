-- One row per valid class session. Fixed version; no secret columns.
WITH v AS (SELECT id, organization_id, week, status, snapshot FROM public.schedule_versions WHERE id = '__VERSION_ID__'),
members AS (
    SELECT se.session_id, COUNT(*) AS student_count
    FROM public.session_enrollments se
    JOIN public.course_sessions s ON s.id = se.session_id
    JOIN v ON v.id = s.version_id
    GROUP BY se.session_id
)
SELECT s.id AS session_id, s.version_id, v.week, v.status AS version_status,
       s.course_id, s.teacher_id, s.room_id, s.day,
       (s.day - v.week) AS weekday_index, s.slot_code,
       s.start_time, s.end_time, c.duration AS duration_minutes,
       r.capacity AS room_capacity,
       LEAST(r.capacity, c.max_size) AS effective_class_capacity,
       COALESCE(m.student_count, 0) AS student_count,
       CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai' AS extracted_at
FROM public.course_sessions s
JOIN v ON v.id = s.version_id
JOIN public.courses c ON c.id = s.course_id
JOIN public.rooms r ON r.id = s.room_id
LEFT JOIN members m ON m.session_id = s.id
WHERE s.status NOT IN ('CANCELLED', 'SUSPENDED')
