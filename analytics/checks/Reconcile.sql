-- Read-only release assertions for the final synthetic dataset and one business week.
-- For a different release, update both version_id and expected counts from its verified manifest.
WITH target AS (
    SELECT id, organization_id, week, status
    FROM public.schedule_versions
    WHERE id = 'fe598178-4945-4441-adeb-016009ce7619'
), lessons AS (
    SELECT s.* FROM public.course_sessions s
    JOIN target v ON v.id=s.version_id
    WHERE s.status='SCHEDULED'
), members AS (
    SELECT e.* FROM public.session_enrollments e
    JOIN lessons s ON s.id=e.session_id
), expected AS (
    SELECT s.id AS session_id, s.teacher_id AS user_id, s.fingerprint FROM lessons s
    UNION
    SELECT s.id, e.student_id, s.fingerprint FROM lessons s
    JOIN members e ON e.session_id=s.id
), checks AS (
    SELECT 1 AS sort_order, '已审核需求' AS metric, 668 AS expected,
           (SELECT count(*) FROM public.enrollment_requests e JOIN target v
              ON e.organization_id=v.organization_id AND e.week=v.week
            WHERE e.status='APPROVED') AS actual
    UNION ALL SELECT 2,'正式课次',68,(SELECT count(*) FROM lessons)
    UNION ALL SELECT 3,'安排学生人次',668,(SELECT count(*) FROM members)
    UNION ALL SELECT 4,'独立选课需求',668,(SELECT count(DISTINCT request_id) FROM members)
    UNION ALL SELECT 5,'覆盖时段',40,(SELECT count(DISTINCT (day,slot_code)) FROM lessons)
    UNION ALL SELECT 6,'应确认事项',736,(SELECT count(*) FROM expected)
    UNION ALL SELECT 7,'已同意且指纹一致',736,
        (SELECT count(*) FROM expected e JOIN public.schedule_confirmations c
           ON c.session_id=e.session_id AND c.user_id=e.user_id
          AND c.fingerprint=e.fingerprint AND c.decision='ACCEPTED')
    UNION ALL SELECT 8,'有效资源预订',804,
        (SELECT count(*) FROM public.resource_reservations r JOIN lessons s
           ON s.id=r.session_id WHERE r.is_active)
    UNION ALL SELECT 9,'生效版本',1,(SELECT count(*) FROM target WHERE status='ACTIVE')
)
SELECT metric,expected,actual,actual=expected AS passed FROM checks ORDER BY sort_order;
