# MicroServiceFL 核心包

本目录是 MicroServiceFL 微服务故障定位智能体的核心 Python 包。

**完整文档（简介、架构、快速开始、部署模式、命令参考、配置项）见项目根目录的 [README.md](../README.md)。**

模块速览：

| 目录 | 职责 |
|------|------|
| `datasource/` | `DataSource` 接口 + DuckDB（离线）/ SkyWalking（在线）实现 |
| `collectors/` | psutil 指标 + 日志采集器（含保留期裁剪） |
| `monitor/` | 统计检测器 + 日志签名检测 + 自主监控循环 |
| `tools/` | `fl_*` 诊断工具（信号 / 拓扑 / 代码映射 / 能力探测） |
| `greybox/` | jar 接口索引生成 + 反编译 |
| `targets/` | 目标系统命名配置（TargetProfile） |

相关文档：数据契约见 [`SCHEMA.md`](SCHEMA.md)；定位方法论见 [`../.claude/skills/locate/SKILL.md`](../.claude/skills/locate/SKILL.md)。
