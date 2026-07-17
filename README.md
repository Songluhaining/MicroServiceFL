<h1 align="center">MicroServiceFL</h1>

<p align="center"><strong>微服务故障定位智能体</strong></p>

<p align="center">
从可观测性数据（调用链 / 指标 / 日志）自动将故障定位到 <strong>服务 → 类 → 方法</strong>，<br>
给出根因分析与修复建议。支持无人值守的实时自动诊断，也支持按需一次性定位。
</p>

<p align="center">基于 <a href="https://github.com/HKUDS/OpenHarness">OpenHarness</a> 智能体框架构建。</p>

---

## 一、简介

给定一个**故障工单**（时间窗口 + 现象），或由内置监控**自主发现**，MicroServiceFL 调用大模型驱动的一组诊断工具，从采集到的可观测性数据中：

1. 定位到出问题的**服务**，并区分**真凶与受害者**（避免把被拖累的上游误判为根因）；
2. 逐级下钻到**接口 → 类 → 方法**；
3. 按故障类型（延迟 / 异常 / 资源）给出**证据支撑的根因**与**修复方向**。

系统采用**灰盒**方式：不依赖源码，通过部署的 jar 包反建接口索引、必要时反编译读取代码；在缺少代码产物或调用链数据时，**自动降级**到可支持的最粗粒度，并如实标注置信度，绝不臆造超出证据的结论。

## 二、核心特性

| 特性 | 说明 |
|------|------|
| **实时自主诊断** | 持续采样各服务 KPI → 统计检测异常 → 自动触发定位 → 落地报告，全程无人值守，分钟级出结果 |
| **细粒度定位** | 服务 / 接口 / 类 / 方法四级，能到哪级由现场数据决定 |
| **多模态融合** | 调用链（SkyWalking / DeepFlow）、指标（CPU/内存）、日志（错误堆栈）三路互补 |
| **能力自适应** | 运行时探测现场有哪些模态数据，按实际能力调整诊断计划并如实上报粒度，同一套代码适配不同数据条件 |
| **可插拔数据源** | 统一 `DataSource` 抽象，离线（DuckDB 回放）与在线（实时查询）一键切换，上层无需改动 |
| **目标系统可配置** | 服务→模块→jar→包 的命名约定收敛在 `TargetProfile`（JSON），接入新系统改配置即可，无需改码 |
| **本地/云端大模型** | 驱动模型兼容 OpenAI 接口，可用云端 API，也可部署内网本地模型（数据不出网） |

## 三、系统架构

诊断流程为五层流水线，前三层为廉价常驻的统计监控，仅在确认异常时唤醒大模型进行深度推理：

```
① 实时采集        →  ② 异常检测       →  ③ 触发定位      →  ④ 定位推理      →  ⑤ 输出报告
指标/日志/调用链      统计基线+3σ +        边沿触发 +         灰盒证据链，       排名候选 +
                     日志签名检测         冷却去重           大模型驱动         结构化 JSON
```

- **数据接入层（`microservice_fl/datasource/`）**：`DataSource` 接口 + 两种实现——`DuckDBDataSource`（离线）、`SkyWalkingDataSource`（在线，调用链实时查 OAP，指标/日志读采集 CSV）。
- **异常检测层（`microservice_fl/monitor/`）**：`StatDetector` 基于中位数 + MAD 的稳健 3σ 检测（资源指标附带绝对下限）；`SignatureDetector` 对日志做内容感知的**新错误签名**检测，捕捉"数量未超阈值但出现了新错误"的低频高危问题。
- **采集层（`microservice_fl/collectors/`）**：psutil 采集各服务 CPU/内存、tail 服务日志（保留异常堆栈），写入带保留期自动裁剪的 CSV。
- **定位能力层（`microservice_fl/tools/` + `/locate` 技能）**：`fl_*` 系列工具（扫服务、拓扑、接口异常、耗时分解、错误 span/日志、接口→类映射、反编译等），由 `/locate` 技能编排大模型按固定方法链推理。
- **能力探测（`fl_capabilities`）**：每次定位先探测数据模态与代码产物，决定最大可达粒度。

## 四、快速开始

### 环境要求

- Python ≥ 3.10
- Java（建接口索引、反编译时需要；仅遥测降级模式可不装）
- 一个兼容 OpenAI 接口的大模型（推荐 DeepSeek-V3，云端 API 或内网自托管均可）

### 安装

```bash
./install-fl.sh                     # 建虚拟环境、装依赖、拉反编译器、自检
# 或手动： pip install -e ".[fl]"
```

### 配置

复制 `fl.env.example` 为 `fl.env` 并按部署环境填写（路径、数据源、SkyWalking 地址等），再配置大模型：

```bash
export DEEPSEEK_API_KEY=sk-...
oh provider use deepseek            # 云端 API
# 或内网本地模型：
oh provider add deepseek-local --provider deepseek --api-format openai \
  --auth-source openai_api_key --model deepseek-v3 --base-url http://<内网地址>/v1
oh provider use deepseek-local
```

### 自检与运行

```bash
fl doctor                           # 一键检查环境、数据源、模型是否就绪
fl build-index --jars <jar目录>     # 从部署的 jar 建接口索引（细粒度定位所需）

# 实时自主诊断（采集器 + 监控循环，通常配合 systemd / nohup 常驻）
fl collect                          # 采集指标 + 日志到 CSV
fl watch                            # 监控 → 检测异常 → 自动定位

# 按需一次性定位
fl locate "time=<起>~<止> symptom=<现象>"
```

## 五、部署模式

| 模式 | 数据源 | 适用场景 |
|------|--------|---------|
| **离线** | DuckDB（`fl ingest` 灌入已采集 CSV） | 回放历史故障、离线评测 |
| **在线** | SkyWalking OAP（调用链）+ 采集 CSV（指标/日志） | 生产环境实时诊断 |
| **降级** | 仅指标 + 日志（无调用链） | 客户环境不具备链路追踪时，定位到服务级 + 异常方法级 |

> 调用链非必需但价值最高：有调用链才能做"真凶/受害者"判定与延迟归因；无调用链时，异常类故障仍可凭日志堆栈定位到类/方法。可选接入 DeepFlow 等 eBPF/抓包工具在**不重启业务、不改代码**的前提下补齐调用链。

## 六、命令参考

| 命令 | 说明 |
|------|------|
| `fl doctor` | 检查环境、数据源、模型的就绪状态 |
| `fl targets` | 列出目标系统配置，显示当前生效的 |
| `fl build-index --jars <目录>` | 扫描 jar 生成接口→类/方法索引 |
| `fl ingest --dataset <目录>` | 将采集的 CSV 灌入 DuckDB（离线模式） |
| `fl init --jars <目录> --data <目录>` | 一步完成建索引 + 灌库 |
| `fl collect [--metric] [--log]` | 运行实时采集器（Ctrl-C 停止，通常常驻） |
| `fl watch` | 自主监控：检测异常并自动定位，报告写入 `incidents/` |
| `fl locate "time=... symptom=..."` | 一次性定位并输出结果 |
| `fl repl` | 交互式定位，逐步观察每次工具调用 |

## 七、定位粒度

粒度由现场数据与代码产物共同决定，`fl_capabilities` 会在每次定位前探测并如实上报：

| 最大粒度 | 前提条件 |
|---------|---------|
| **方法** | 有接口索引且 jar 可反编译（完整灰盒）；或异常故障日志堆栈带业务帧 |
| **类** | 有接口索引但 jar 加密不可反编译 |
| **接口 / 服务** | 仅遥测数据（无索引/无 jar，或无调用链） |

## 八、配置项（环境变量）

| 变量 | 默认 | 含义 |
|------|------|------|
| `OH_FL_DATASOURCE` | `duckdb` | 数据源：`duckdb`（离线）/ `skywalking`（在线） |
| `OH_FL_DB` | `<dataset>/fl.duckdb` | DuckDB 库路径（离线） |
| `OH_FL_INDEX` | `<dataset>/endpoint_index.json` | 接口→类/方法索引 |
| `OH_FL_JARS` | — | 部署的 jar 目录（建索引 / 反编译） |
| `OH_FL_CFR` | `~/tools/cfr-0.152.jar` | CFR 反编译器 jar |
| `OH_FL_SKYWALKING_URL` | `http://127.0.0.1:12800/graphql` | OAP GraphQL 地址（在线） |
| `OH_FL_SKYWALKING_TZ_OFFSET` | `0` | 输入时间窗相对 UTC 的小时偏移（东八区填 8） |
| `OH_FL_METRIC_CSV` / `OH_FL_LOG_CSV` | `<dataset>/…` | 采集器写入的实时指标 / 日志 CSV |
| `OH_FL_RETENTION_HOURS` | `24` | 指标/日志 CSV 的保留时长，超期自动裁剪 |
| `OH_FL_TARGET` | 内置 `yudao-cloud` | 目标系统命名配置（`~/.openharness/fl_targets/<名>.json`） |

## 九、目录结构

```
microservice_fl/
├── datasource/      DataSource 接口 + DuckDB / SkyWalking 实现
├── collectors/      psutil 指标 + 日志采集器（含保留期裁剪）
├── monitor/         统计检测器 + 日志签名检测 + 自主监控循环
├── tools/           fl_* 诊断工具（信号 / 拓扑 / 代码映射 / 能力探测）
├── greybox/         jar 接口索引生成 + 反编译
├── targets/         目标系统命名配置（TargetProfile）
├── config.py        路径与配置
└── ingest.py        CSV → DuckDB
```

> 定位方法论（RCA 方法链与输出契约）见 `.claude/skills/locate/SKILL.md`；数据契约见 `microservice_fl/SCHEMA.md`。
>
> 本项目基于开源智能体框架 [OpenHarness](https://github.com/HKUDS/OpenHarness) 构建，其原始文档见提交历史。
