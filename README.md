# EduFlow · 教培排课与运营分析

**本地测试环境 · 合成数据，仅供测试与作品演示**

EduFlow 将师生可用时间、教师资质、教室容量与选课偏好转化为可配置的排课规则，把选课、审核、试排、校验、发布和确认串成一条可追踪的业务流程。
在本地合成数据测试中，两版均安排 668/668 条已审核需求、覆盖 40/40 个教学时段。偏好优先策略将首选命中率从 15.87% 提升至 30.84%。五页 Power BI 报表同时展示偏好收益、额外开班投入和确认质量。

[在线查看五页分析报告](docs/portfolio/EduFlow-Case-Study.pdf) · [交互 PBIX 与复现说明](analytics/README.md) · [三端演示截图](docs/portfolio.md)

## 单周模拟结果

| 指标 | 结果 |
|---|---:|
| 独立学生 | 220 |
| 已安排的审核需求 | 668 / 668 |
| 教学时段覆盖 | 40 / 40 |
| 生效课次 | 68 |
| 已同意的必要确认 | 736 / 736 |
| 独立校验硬冲突 | 0 |

同一批需求的首选时段命中率由 **15.87% 提高到 30.84%**。优化版多开 18 个课次，教室座位利用率由 **60.07% 降至 42.49%**。本次模拟中，发布到生效用时约 **1 分 32 秒**，必要确认由模拟账号通过正常接口批量提交。 [分析结论](analytics/ANALYSIS.md) · [指标口径](analytics/METRICS.md) · [两版对比数据](analytics/data/ScheduleComparison.csv)

## 实施与验收 SOP
| SOP 环节 | 操作与验收产出 |
|---|---|
| 数据准备与规则确认 | 梳理课程、师生、教师资质、教室、可用时段和偏好字段，形成已审核需求快照与约束清单。 |
| 试排与方案比选 | 用同一批输入运行资源均衡和偏好优先策略，比较需求满足、偏好命中、课次与资源投入，独立校验硬约束。 |
| 发布、确认与异常处理 | 管理员发布通过校验的候选方案，师生按课次确认。收齐后复核资源与版本，再整周生效。异常进入调整流程。 |
| 复盘与机构接入 | 按课表版本导出 SQL 快照，用 Power BI 复盘并对账。接入新机构时复用字段映射、规则清单和验收流程，由机构提供业务数据与接口授权。 |

## 本机启动

准备 Windows PowerShell、Python 3.12、Node.js/npm 和启用了 Linux 容器的 Docker Desktop。打开项目根目录，等待 Docker 引擎就绪后执行：

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe .\run.py --no-browser
.\.venv\Scripts\python.exe .\run.py doctor
```

`setup.ps1` 使用锁文件安装依赖、构建两个前端，并在首次运行时生成本机配置。已有环境可直接启动。只准备开发环境和构建时使用 `.\setup.ps1 -SkipContainers`。VS Code 的「任务」菜单也提供启动和后端测试入口。 [部署步骤](docs/deployment.md)

| 本机入口 | 角色 |
|---|---|
| `http://127.0.0.1:8000/login` | 管理员 `admin` |
| `http://127.0.0.1:8100/login` | 教师 `teacher01`、学生 `student001` |

公开配置示例中的 `DEMO_PASSWORD` 供本机合成数据演示使用。教师和学生使用相互独立的浏览器会话。启动时为演示库初始化本地账号和业务数据，复现时使用当前界面可选的未来演示周。 [使用手册](docs/user-guide.md)

## 复现与验收

- **排课与部署**沿用公开的迁移、默认演示数据和三组求解场景。控件、账号和数据库均配置为本机演示。
- **分析**使用 `analytics/data/` 中的冻结快照。查询覆盖需求、课次、教室时段、确认四个事实集，并提供 DAX、指标字典、对比 SQL 和九项最终版对账。
- **企业接入**从空机构和独立数据库开始。企业自备业务数据、外部系统接口与访问授权，按数据准备、字段映射、权限配置、联调和目标环境验收的顺序推进。 [空机构初始化](docs/deployment.md#空机构初始化) · [API 说明](docs/api-design.md)

[架构](docs/architecture.md) · [数据库](docs/database.md) · [业务规则](docs/business-rules.md) · [排课规则](docs/scheduling-rules.md) · [求解策略](docs/scheduling-algorithm.md) · [本地验收结果](docs/testing-report.md)

项目源码按 [Apache License 2.0](LICENSE) 提供。第三方依赖遵循各自的许可。
