WITH v AS (
    SELECT id, week, status, base_version_id
    FROM public.schedule_versions
    WHERE id = '__VERSION_ID__'
), sessions AS (
    SELECT s.*, v.week, v.status AS version_status
    FROM public.course_sessions s
    JOIN v ON v.id = s.version_id
), changed_old AS (
    SELECT s.id AS current_session_id, old.id AS old_session_id,
           old.teacher_id
    FROM sessions s
    JOIN v ON v.id = s.version_id
    JOIN public.course_sessions old
      ON old.version_id = v.base_version_id AND old.logical_id = s.logical_id
    WHERE old.fingerprint <> s.fingerprint
), required AS (
    SELECT id AS session_id, teacher_id AS user_id FROM sessions
    UNION
    SELECT s.id, m.student_id FROM sessions s
    JOIN public.session_enrollments m ON m.session_id = s.id
    UNION
    SELECT current_session_id, teacher_id FROM changed_old
    UNION
    SELECT o.current_session_id, m.student_id FROM changed_old o
    JOIN public.session_enrollments m ON m.session_id = o.old_session_id
), tasks AS (
    SELECT s.id || '|' || r.user_id AS confirmation_task_id,
           f.id AS confirmation_id, s.version_id, s.week, s.version_status,
           s.id AS session_id, s.course_id, s.teacher_id, s.room_id,
           s.day, s.slot_code, r.user_id, u.role AS participant_role,
           CASE
               WHEN f.id IS NULL THEN 'MISSING'
               WHEN f.fingerprint <> s.fingerprint THEN 'STALE'
               ELSE f.decision
           END AS decision,
           f.created_at AT TIME ZONE 'Asia/Shanghai' AS created_at, f.confirmed_at AT TIME ZONE 'Asia/Shanghai' AS confirmed_at, f.reason,
           CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Shanghai' AS extracted_at
    FROM required r
    JOIN sessions s ON s.id = r.session_id
    JOIN public.users u ON u.id = r.user_id
    LEFT JOIN public.schedule_confirmations f
      ON f.session_id = r.session_id AND f.user_id = r.user_id
)
SELECT tasks.*, CASE WHEN reason LIKE 'DEMO_SIMULATION:%' THEN '模拟账号确认' WHEN confirmation_id IS NOT NULL THEN '账号本人确认' ELSE '尚未确认' END AS confirmation_source,
       CASE participant_role
           WHEN 'TEACHER' THEN '教师'
           WHEN 'STUDENT' THEN '学生'
           ELSE participant_role
       END AS participant_role_name,
       CASE decision
           WHEN 'PENDING' THEN '待确认'
           WHEN 'ACCEPTED' THEN '已同意'
           WHEN 'REJECTED' THEN '已拒绝'
           WHEN 'MISSING' THEN '待生成'
           WHEN 'STALE' THEN '待重新确认'
           ELSE decision
       END AS decision_name
FROM tasks
