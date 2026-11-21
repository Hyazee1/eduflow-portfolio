# API

完整接口定义见各入口运行服务的 /openapi.json。交互式 /docs 默认关闭，避免从外部 CDN 加载文档脚本。前缀 /api/v1；Bearer 访问令牌，HttpOnly 刷新 Cookie，支持轮换与撤销。

关键写入携带 8—128 字符 Idempotency-Key，相同键与内容返回原结果，不同内容冲突。更新提交 version。错误包含稳定 code、title、request_id、details。

| 能力 | 主要路径 |
|---|---|
| 身份 | /auth/login, /auth/refresh, /auth/logout, /auth/me |
| 配置 | /bootstrap, /time-slot-templates, /admin/rules |
| 教室开放时段 | GET/POST /admin/rooms/{id}/availability；写入包含 version、slots 和 Idempotency-Key |
| 选课 | /courses, /enrollment-requests, /enrollment-requests/mine |
| 审核 | /teachers/me/offering-reviews, /teacher-decisions, /admin/teacher-decisions, /admin/offerings/{id}/reviews |
| 求解 | /admin/schedule/solve, /admin/schedule/jobs/{id} |
| 版本 | /admin/schedule/versions/{id} 及 /validate, /publish, /cancel, /delete, /sessions |
| 课表 | /students/me/schedule, /teachers/me/schedule, /admin/schedule |
| 确认 | /schedule-confirmations |
| 异常 | /change-requests、/mine、/{id}、/{id}/simulate、/{id}/cancel；/admin/change-requests、/{id}/decision |
| 运营 | /admin/analytics, /notifications, /classes/{record_id}/roster, /attendance, /attendance/batch, /feedback |
| 紧急停课 | POST /admin/sessions/{record_id}/pause，管理员提交 reason 与幂等键 |
| Agent | /agent/tools, /agent/chat |

管理员列表支持分页、搜索及适用的周/身份筛选。资源修改使用字段白名单，非管理员查询受对象归属限制。教师 Agent 只读取本人授课班次关联的学生需求。教室开放时段的乐观版本冲突返回 409；破坏生效课表的修改整体回滚。

POST /admin/schedule/versions/{id}/delete 携带 version 与 Idempotency-Key，返回 {id, deleted: true}。仅管理员可删除同机构、从未发布且无依赖的 DRAFT、VALIDATED、CANCELLED 候选；拒绝过期 revision、业务引用和曾发布历史。删除与发布使用同一事务锁协议，清理课次/成员/快照，已完成任务解除版本关联、保留精简结果和删除审计。版本详情含 can_delete、delete_block_reason、requires_resolve、pending_request_count；质量指标含 covered_slots、teacher_daily_rest 和 minimum_adjacent_day_rest。

课表 id 是逻辑课次 ID，供确认与调课；record_id 是版本记录 ID，供名单、出勤、反馈及暂停。课程目录提供实际班次和余位，未生效标记 provisional；版本含 unmet_details，方案含授权范围内可读名称。

课表响应的 version_slots 按版本 ID 返回保存的时段快照，前端切换版本时同步选择时间轴；slots 为默认展示版本的轴。Agent 工具的排课周必须为合法日期且为周一，错误输入返回 422。

/health/live 检查存活，/health/ready 检查数据库、Redis、模板和 Worker 心跳。正式应用不存在测试时钟接口。

求解 `POST /admin/schedule/solve` 接受 `week`，可选 `optimization_profile`（`balanced` 默认，或 `preference_first`）及 `reference_version_id`。返回异步任务编号；预算来自规则配置，阶段状态、偏好、实际耗时和保留方案保存在结果报告。参考版本必须同机构同周，并通过源数据与版本检查。

发布 `POST /admin/schedule/versions/{id}/publish` 保留 `version`，可选成对提交 `replace_expired_version_id` 与 `replace_expired_version_revision`。仅可替换明确选定的逾期待确认版本；新候选检查、旧版取消、新版发布和通知在同一锁与事务中完成，任何错误完整回滚。正常发布不传替换字段仍兼容。确认接口的 `reason` 可保存模拟操作标识，不改变决定、参与人、指纹或期限的验证规则。


## 两种入口与账号接口

管理服务默认 8000，只接受 ADMIN；师生服务默认 8100，只接受 TEACHER / STUDENT。师生 OpenAPI 不注册 /api/v1/admin 路由，即使持有管理员令牌也不能调用。跨入口签名、audience、用途与会话不互认。

| 方法与路径（均在 /api/v1 下） | 权限与说明 |
|---|---|
| GET /auth/config | 匿名，只返回入口、演示标记与公网关闭状态 |
| POST /auth/login、/auth/refresh、/auth/logout | 各入口允许身份；HttpOnly 刷新 Cookie 分别命名 |
| GET /auth/sessions | 本人会话，明确字段，不返回令牌摘要或原文 |
| POST /auth/sessions/{id}/revoke | 本人会话，幂等键；不能撤销他人会话 |
| POST /auth/password | 原密码验证后改密，并撤销全部旧会话；成功后重新登录 |
| POST /auth/accept | 匿名，接受 INVITE / RESET 凭证并设置密码，限流及一次使用 |
| POST /admin/invitations | 管理员，预绑定师生角色、24 小时有效，幂等键支持同内容复用 |
| POST /admin/users/{id}/account-token | 管理员，为停用/待邀请账号生成邀请或为有效账号生成重置凭证 |
| POST /admin/users/{id}/revoke-sessions | 管理员撤销目标账号全部会话 |
| POST /admin/users | 现有账号创建/修改/停用，角色与版本检查；不能停用当前登录账号 |

刷新 Cookie 的路径为 /api/v1/auth，SameSite=Strict，HTTP 本机不启用 Secure，HTTPS 测试启用 Secure。浏览器写请求携带同入口 Origin；缺少 Origin 的认证 Cookie 请求须带 X-EduFlow-Request: 1。错误返回稳定 code 与 request_id；密码、令牌、输入原文和机器路径不进入错误正文。

列表统一 items / total / page / page_size，page_size 上限 500。个人账号与档案采用明确字段；学生端不返回内部 CRM 跟进备注。正常响应包含 X-Request-ID 和 Server-Timing: app;dur=...，供本机性能测量；不包含路径或机器标识。

邀请链接在浏览器 fragment 携带凭证，打开后立即清除，不进入 URL 查询参数或服务器访问日志。只通过可信方式交付本人，原始链接不得放入展示截图或公开文档。
