# 本地部署与运维

## 依赖和启动

已验证环境为 Windows、Python 3.12.14、Node 26 与 Docker Linux 容器。后端运行时和开发环境分别使用 `backend/requirements.lock.txt`、`backend/requirements-dev.lock.txt`，安装工具使用 `backend/tooling.lock.txt` 固定 pip 26.2 及哈希，前端使用 `frontend/package-lock.json`。基础镜像 digest 固定在 Compose / Dockerfile，应用镜像本机构建。

`setup.ps1 -Python312 <解释器路径>` 可指定 Python 3.12；`-SkipContainers` 只准备项目依赖与前端。脚本保留已有 `.env`，缺失时生成随机数据库密码和签名密钥。浏览器测试引擎可按需在 frontend 执行 `npm exec playwright install chromium firefox webkit`，将 `PLAYWRIGHT_BROWSERS_PATH` 指向项目 `.runtime/browsers`。

```powershell
python run.py                  # 幂等启动，健康后打开登录页
python run.py --no-browser     # 保持无浏览器启动
python run.py --rebuild        # 代码更新：先备份、迁移，再重建共享镜像
python run.py --prepare-only   # 准备数据库与时段，暂不启动应用
python run.py doctor           # 检查两个入口
```

| 服务 | 监听 | 数据与权限 |
|---|---|---|
| 管理应用 | 127.0.0.1:8000 | ADMIN，独立密钥与 Cookie |
| 师生应用 | 127.0.0.1:8100 | TEACHER / STUDENT，管理路由不存在 |
| PostgreSQL16 | 127.0.0.1:5432 | 本地命名卷 postgres_data |
| Redis7 | 127.0.0.1:6379 | 本地命名卷 redis_data，默认业务 DB0 |
| Worker / Beat | 容器网络 | 一个求解占用名额，并发 2 保留轻任务处理余量 |

应用镜像以 UID10001 运行，根文件系统只读、移除 capability、禁止提权，临时文件只在 /tmp。HTTP 应用仅得到自己的签名密钥与 DML 数据库连接；Worker 不得到 HTTP 密钥，维护容器才具有恢复所需的数据库创建权限。没有 Docker socket 或个人目录挂载。

端口占用会提示冲突。在本项目根目录执行 `docker compose stop` 可停止服务并保留数据卷。迁移失败时，启动器保留升级前加密备份和私有启动日志，并停止后续步骤。

## 空机构初始化

演示库与演示账号继续保留。要建立空机构，应在独立部署副本和全新 PostgreSQL 数据库上操作；不对演示库运行清理。首次初始化需要具备建表/迁移权限的数据库连接。

1. 将私有 `.env` 中 `DATABASE_URL` / `POSTGRES_DB` 指向新库，设置 `ORGANIZATION_SLUG` 为机构标识、`INITIALIZATION_MODE=blank`、`INSTITUTION_NAME`、`INITIAL_ADMIN_USERNAME`。
2. 运行 `python scripts/runtime_config.py --files-only` 以建立私有目录权限。在 `.runtime/private/secrets/initial-admin.password` 私有文件中保存至少 12 位的首位管理员密码，不放入命令参数或文档。
3. 在 `.env` 设置 `INITIAL_ADMIN_PASSWORD_FILE=.runtime/private/secrets/initial-admin.password`，再执行 `python run.py --rebuild`。首次只生成机构、8 个时段、规则与管理员，不生成学生、教师、课程或演示密码账号。
4. 初始化后删除首次密码文件。后续启动先检查机构是否已存在，不再需要它，也不会重置账号或业务。用管理员“账号与安全”邀请师生；教师资格、课程、教室和可用时间按实际情况建立。

同一电脑同时运行两个副本时，请为每个实例配置独立的 Compose 项目名、镜像、回环端口和数据卷，并同步应用的 Origin 与启动器的健康检查端口。单实例按默认设置使用 8000 和 8100。

## 加密备份与恢复

```powershell
python run.py backup
python run.py restore --archive <文件名.dump.enc> --target <全新名称_test或_restored>
```

备份在 `.runtime/private/backups`，采用 PostgreSQL16 自定义格式与 cryptography Fernet 认证加密。密钥 `.runtime/private/secrets/backup.key` 单独保存；丢失密钥无法恢复，泄露时应重新制定备份策略。备份归档不能代替单独保存密钥，也不能把二者一起公开。

恢复只允许新的 *_test / *_restored 数据库，拒绝现有库和应用库；先解密认证，再创建库并以单事务恢复。恢复验证后若要切换正式库，由操作者明确调整私有连接配置并重启，不自动覆盖当前服务。

现有 Beat 每天北京时间 03:00 安排加密备份；Worker 启动时检查最近有效备份，超过 24 小时则补做。失败有限重试并向管理员发送去重站内告警，原有恢复点保留。每日备份在新归档认证和摘要验证成功后保留最近 7 份，`protected.json` 指定的恢复点单独保留。升级前仍另外备份；手动恢复点不会被误作日常备份删除。不上传第三方。

六个容器使用 Docker 日志轮转，每份 10MB、最多 3 份；启动器日志每份 5MB、最多 3 份。数据库升级、备份和恢复的原始记录在 `.runtime/private`，不进入公开材料。不能把容器重启策略当作断电、睡眠或 Docker 退出期间仍可用的承诺。

## 网络、模型和开发

公网关闭且只绑定回环地址，不建立隧道、路由器或防火墙规则。不能只把绑定地址改为 0.0.0.0 就对外使用。`.env.example` 提供入口 Origin 等配置占位，默认 Uvicorn 不信任转发头。部署到机构网络时按目标环境配置受信任的 HTTPS、反向代理、来源校验和资源权限。

默认容器固定离线 Agent。在线网关保留，可在本地开发配置 `AGENT_MODE=online`、`LLM_BASE_URL`、`LLM_MODEL`、`LLM_API_KEY`；容器需显式使用独立私有 Compose 覆盖配置。未经配置不会发生模型请求。本轮没有使用真实密钥或调用真实服务商。

Windows 调试使用项目 .venv；默认演示使用容器。前端开发服务器需按入口配置本地代理与 Origin，不能据开发服务结果代替容器验收。集成测试只使用新建的 `*_test` 库。浏览器测试的演示库为 `eduflow_ai_e2e_test`，默认入口为 8001 和 8101，其中的重置操作仅适用于该专用测试库。


## 机构接入和验收

1. 在空机构中录入课程、教师资质、教室、设备和可用时段，再引入选课与偏好数据。
2. 由企业提供数据、接口文档和访问授权，确定字段、角色与回写范围。在测试环境联调身份验证、幂等、重试和回退。
3. 在目标环境使用代表性样本验证选课、审核、求解、发布、师生确认、异常调整及备份恢复。通过目标环境验收后再开放真实入口。

独立初始化生成新的数据周与课表版本。作品集中的既有报告使用公开的固定合成快照。 [使用手册](user-guide.md) · [分析工程](../analytics/README.md) · [验收结果](testing-report.md)
