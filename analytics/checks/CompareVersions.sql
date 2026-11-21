-- Read-only comparison of two synthetic versions of the same organization and week.
WITH targets (display_order, comparison, version_id) AS (
    VALUES
        (1, 'baseline', '2ec99418-cbce-4c42-af3a-1a5c0d357973'::text),
        (2, 'optimized', 'fe598178-4945-4441-adeb-016009ce7619'::text)
), versions AS (
    SELECT t.display_order, t.comparison, v.id AS version_id, v.organization_id,
           v.week, v.status, v.snapshot, v.published_at, v.activated_at
    FROM targets t
    JOIN public.schedule_versions v ON v.id = t.version_id
), approved AS (
    SELECT v.version_id, e.id AS request_id
    FROM versions v
    JOIN public.enrollment_requests e
      ON e.organization_id = v.organization_id AND e.week = v.week
    WHERE e.status = 'APPROVED'
      AND EXISTS (
          SELECT 1
          FROM json_array_elements(v.snapshot -> 'demands') d
          WHERE d.value ->> 'id' = e.id::text
      )
), lessons AS (
    SELECT v.version_id, s.id AS session_id, s.day, s.day - v.week AS day_index,
           s.slot_code, c.duration, r.capacity
    FROM versions v
    JOIN public.course_sessions s ON s.version_id = v.version_id
    JOIN public.courses c ON c.id = s.course_id
    JOIN public.rooms r ON r.id = s.room_id
    WHERE s.status NOT IN ('CANCELLED', 'SUSPENDED')
), placements AS (
    SELECT l.version_id, e.request_id, l.day_index, l.slot_code
    FROM lessons l
    JOIN public.session_enrollments e ON e.session_id = l.session_id
), preferences AS (
    SELECT p.request_id, p.day_index, p.slot_code, p.rank,
           MIN(p.rank) OVER (PARTITION BY p.request_id) AS first_rank
    FROM public.enrollment_time_preferences p
    WHERE EXISTS (SELECT 1 FROM approved a WHERE a.request_id = p.request_id)
), request_matches AS (
    SELECT a.version_id, a.request_id,
           BOOL_OR(p.request_id IS NOT NULL) AS scheduled,
           BOOL_OR(f.rank = f.first_rank) AS first_match,
           BOOL_OR(f.request_id IS NOT NULL) AS any_match
    FROM approved a
    LEFT JOIN placements p
      ON p.version_id = a.version_id AND p.request_id = a.request_id
    LEFT JOIN preferences f
      ON f.request_id = p.request_id AND f.day_index = p.day_index AND f.slot_code = p.slot_code
    GROUP BY a.version_id, a.request_id
), demand_totals AS (
    SELECT version_id, COUNT(*) AS approved_demands,
           COUNT(*) FILTER (WHERE scheduled) AS fulfilled_demands,
           COUNT(*) FILTER (WHERE first_match) AS first_preferred,
           COUNT(*) FILTER (WHERE any_match) AS any_preferred
    FROM request_matches
    GROUP BY version_id
), lesson_totals AS (
    SELECT version_id, COUNT(*) AS sessions,
           COUNT(DISTINCT (day, slot_code)) AS covered_slots,
           SUM(duration) AS teaching_minutes, SUM(capacity) AS room_seats
    FROM lessons
    GROUP BY version_id
), enrollment_totals AS (
    SELECT version_id, COUNT(*) AS student_assignments
    FROM placements
    GROUP BY version_id
)
SELECT v.comparison, v.version_id, v.week, v.status AS version_status,
       d.approved_demands, d.fulfilled_demands, d.first_preferred, d.any_preferred,
       l.sessions, l.covered_slots, e.student_assignments, l.teaching_minutes, l.room_seats,
       ROUND(100.0 * d.first_preferred / NULLIF(d.fulfilled_demands, 0), 2) AS first_preference_pct,
       ROUND(100.0 * d.any_preferred / NULLIF(d.fulfilled_demands, 0), 2) AS any_preference_pct,
       ROUND(100.0 * e.student_assignments / NULLIF(l.room_seats, 0), 2) AS room_seat_pct,
       ROUND(EXTRACT(EPOCH FROM (v.activated_at - v.published_at)), 2) AS publish_to_active_seconds
FROM versions v
JOIN demand_totals d USING (version_id)
JOIN lesson_totals l USING (version_id)
JOIN enrollment_totals e USING (version_id)
WHERE (SELECT COUNT(*) FROM versions) = 2
  AND (SELECT COUNT(DISTINCT (organization_id, week)) FROM versions) = 1
ORDER BY v.display_order;
