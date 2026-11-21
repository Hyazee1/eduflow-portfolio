# EduFlow 排课与运营分析

**本地测试环境 · 合成数据，仅供测试与作品演示**

这套分析固定到 **2026-11-09 业务周**的生效版本 `fe598178-4945-4441-adeb-016009ce7619`。五页报表依次展示优化对比、运营总览、需求与偏好、资源与全天覆盖、发布和确认质量。 [在线查看五页 PDF](../docs/portfolio/EduFlow-Case-Study.pdf)

## 查看交互报表

从作品集 Release 的附件下载 `EduFlow_Portfolio.pbix`，用 Windows Power BI Desktop 打开。默认使用内嵌合成快照，`pDataMode=Snapshot`，`pDataFolder` 为空。进入「需求与偏好匹配」筛选课程分类和年级，观察指标与明细联动。模型视图可以查看课程维度、需求表、课次表等关系。

第 5 页时间卡片按时分秒显示。本次模拟从发布到生效用时 **91.69 秒**，依据 `DimVersion` 中的两条数据库时间戳。736 项确认由测试账号提交。确认矩阵按对应课次所属日期归组。 [时间数据](data/DimVersion.csv) · [指标口径](METRICS.md)

## 复核数据和 SQL

| 文件或目录 | 内容 |
|---|---|
| [事实与维度 CSV](data/) | 11 份业务快照，以及课程分类映射 `MapCourseGroup.csv`。 |
| [数据清单](dataset-manifest.json) | 11 份业务 CSV 的字段、行数、快照版本和 SHA-256。 |
| [两版对比 SQL](checks/CompareVersions.sql) · [汇总 CSV](data/ScheduleComparison.csv) | 对同周的两个课表版本分别汇总一行，保留排课结果、命中数量、覆盖、课时与座位利用率。 |
| [分表查询](sql/) · [最终版核对 SQL](checks/Reconcile.sql) | 使用只读 PostgreSQL 查询核对历史版本。 |
| [DAX](measures.dax) · [分析结论](ANALYSIS.md) | 展示指标定义、分母、偏好提升和资源取舍。 |

在保有对应历史版本的 PostgreSQL 中执行 `Reconcile.sql`，应返回九行且 `passed` 均为 true。单表查询里的 `__VERSION_ID__` 填入目标版本。仓库已带冻结的历史分析快照，新安装的独立演示库会生成自己的版本和周。

## 刷新自己生成的合成数据

在本机生成并生效一份新课表后，从项目根目录执行以下命令，填写该课表的版本编号：

```powershell
.\.venv\Scripts\python.exe .\scripts\export_analytics.py --version-id '<新生效版本编号>'
```

在 Power BI 的「转换数据 → 管理参数」中保持 `pDataMode=Snapshot`，将 `pVersionId` 和 `pDataFolder` 调整为新导出的版本与本地 CSV 目录，然后刷新。清空文件夹参数时回到随文件保存的快照。模型同时保留 PostgreSQL 参数，现场接入时由操作者使用自己的数据库及凭据。
