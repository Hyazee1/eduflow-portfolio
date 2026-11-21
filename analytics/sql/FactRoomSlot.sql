-- One row per open room/date/teaching-slot; includes unoccupied slots.
-- Current template revision is 1. Scope to the selected version/week.
WITH v AS (SELECT id,organization_id,week,status FROM public.schedule_versions WHERE id='__VERSION_ID__'),
open_slots AS (
 SELECT v.id AS version_id,v.week,r.id AS room_id,(v.week+a.day_index) AS day,
        a.day_index AS weekday_index,a.slot_code,t.duration_minutes,
        ((v.week+a.day_index)+t.start::time) AT TIME ZONE 'Asia/Shanghai' AS starts_at,
        ((v.week+a.day_index)+t.end::time) AT TIME ZONE 'Asia/Shanghai' AS ends_at
 FROM v
 JOIN public.availability_rules a ON a.organization_id=v.organization_id AND a.owner_kind='ROOM'
 JOIN public.rooms r ON r.id=a.owner_id AND r.status='OPEN'
 JOIN public.time_slot_templates t ON t.organization_id=v.organization_id
      AND t.revision=1 AND t.code=a.slot_code AND t.is_bookable
),
valid_slots AS (
 SELECT o.* FROM open_slots o
 WHERE NOT EXISTS (
  SELECT 1 FROM public.availability_exceptions x
  WHERE x.owner_id=o.room_id AND x.starts_at<o.ends_at AND x.ends_at>o.starts_at
 )
)
SELECT o.version_id,o.week,o.room_id,o.day,o.weekday_index,o.slot_code,
       o.duration_minutes AS available_minutes,COUNT(s.id) AS scheduled_sessions,
       CASE WHEN COUNT(s.id)>0 THEN o.duration_minutes ELSE 0 END AS planned_minutes
FROM valid_slots o
LEFT JOIN public.course_sessions s ON s.version_id=o.version_id
     AND s.room_id=o.room_id AND s.day=o.day AND s.slot_code=o.slot_code
     AND s.status NOT IN ('CANCELLED','SUSPENDED')
GROUP BY o.version_id,o.week,o.room_id,o.day,o.weekday_index,o.slot_code,o.duration_minutes
