WITH v AS (SELECT id,organization_id,week,status FROM public.schedule_versions WHERE id='__VERSION_ID__')
SELECT r.id AS room_id, r.name AS room_name,
       replace(replace(replace(replace(r.name,'无人机与机器人实验室','无人机'),'实验室',''),'教室',''),' ','') AS room_short_name,
       r.kind,r.capacity,r.status
FROM public.rooms r JOIN v ON v.organization_id=r.organization_id
