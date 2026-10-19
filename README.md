# 政策会诊 · 多智能体政策咨询会诊系统

**AIC 2026（第八届全球校园人工智能算法精英大赛）智能体互联赛道参赛作品**

把"我适不适用这项政策"交给一支**七个各司其职的智能体会诊团**：主持接诊澄清需求 → **条文核查官、落地顾问、风险审查官**三位专家并行独立作答 → 交叉质询互相挑错（风险审查官主导质询、用户代言人替用户追问、条文核查官可随时联网调取政策原文）→ 仲裁官汇总裁决，最终输出一份**每条结论都标注条文出处与置信度**的《会诊意见书》。

全程基于 AIP 国家标准协议（mTLS 双向认证 + AMP 分布式账本留痕）完成真实的跨智能体调用，不是提示词模拟。

## 为什么需要"会诊"而不是一个智能体

政策咨询的难点不是"答不出来"，而是**裸大模型在两个方向都会错**：

| 场景 | 裸 LLM | 政策会诊 |
|---|---|---|
| 政策原文**不公开/制度不存在**（如"南京市市级重点实验室认定"） | 大概率编出一套听起来合理的申报条件 | 多轮检索无果后如实输出**【待核实】**，并给出"疑不存在"的判断依据 |
| 政策原文**公开可查**（如高企认定 国科发火〔2016〕32号） | 凭训练记忆作答，条款引用时有张冠李戴 | 检索官抓回**第十一条全部八项认定条件的原文摘录**，逐项比对后作答，并纠正了提问中错误的条款引用 |

第二个案例中，系统单轮会诊完成 18 次联网检索 + 3 次原文抓取，意见书引用原文 19 处，逐项给出 0.35–0.95 的差异化置信度。

## 七个角色

| 角色 | 文件 | 职责 |
|---|---|---|
| 会诊主持（Leader） | `agents/host.py` | 唯一对外入口；澄清需求、编排三阶段、交付意见书 |
| 条文核查官 | `agents/statute.py` | 事实底座；条文级引用+来源档位标注；可委派本队检索官与平台外部智能体取证 |
| 落地顾问 | `agents/advisor.py` | 条文 → 可执行清单（材料/流程/时限/额度） |
| 风险审查官 | `agents/challenger.py` | 六维质询：时效/地域/例外/门槛/误读/冲突 |
| 仲裁官 | `agents/arbiter.py` | 分歧量化、置信度裁决、三段式意见书 |
| 用户代言人 | `agents/persona.py` | 扮演具体身份（如"小微企业管理者"）替用户追问 |
| 政策原文检索官 | `agents/researcher.py` | 联网检索（web-search API）+ 政府网站原文抓取 + 权威度分级 |

## 三阶段会诊流程

```
用户（平台"叮当"或任意 AIP 客户端）
  │
  ▼
会诊主持 ── 要素澄清（领域/对象/时间缺一先问）
  │
  ├─ 阶段一 独立作答 ── 条文核查官 ∥ 落地顾问 ∥ 风险审查官（互相不可见）
  │
  ├─ 阶段二 交叉质询 ── 风险审查官主导；用户代言人追问
  │      └─ 条文核查官缺原文时 → 委派 政策原文检索官 → 联网检索 → 条款级摘录回填
  │             （本队检索官覆盖不了时 → 经发现服务委派平台外部智能体，结果在意见书中披露）
  │
  └─ 阶段三 仲裁裁决 ── 仲裁官汇总 → 《会诊意见书》
                         （有据结论 / 存分歧点 / 待核实项 + 下一步行动清单）
```

每次"主持→专家"、"核查官→检索官"的委派都是一次真实的跨身份 mTLS 调用，本地 AMP 账本留痕、由转发器上报至平台计量。

## 与平台智能体生态互联

条文核查官在取证环节可经官方发现服务（`acps_discover`，由 `AipDelegationHooks` 注入）检索平台上的其他队伍智能体，并按 AIP 协议委派调用——按文献类型分流外聘（本队检索官负责政策文件，法条库类智能体负责法律原文，实测已从其他队伍的智能体成功逐字取回法条）。所有外部调用在《会诊意见书》的**"外部智能体调用记录"段**逐条披露：名称、AIC 尾号、调用目的、结果（含"发现但未调用""已放弃"），本队七人与外部智能体以 AIC 名单严格区分，全程任务账本可审计。

委派能力的分层是设计而非省略：只有**会诊主持**（编排权）与**条文核查官**（取证权）挂载委派钩子，其余五位为终端专家——每个外呼发起者可审计，仲裁官不得私自引入未经质询的外部证据。

## 快速开始

环境：Python 3.12+（Windows/Linux 均可，Windows 兼容补丁在 `agents/rtbase.py` 内置生效）。

```bash
# 1) 安装依赖（框架 wheel 从梧桐平台下载：https://wt.ioa.pub/flux/ 首页 Downloads）
python -m venv .venv
.venv/Scripts/pip install acps_sdk-2.2.0-py3-none-any.whl wit_framework-2.1.0-*.whl

# 2) 配置（agents 目录）
cd agents
cp llm.env.example llm.env        # 填：大模型端点/密钥、检索API密钥、Leader公网隧道地址
cp .acps.env.example .acps.env    # 填：梧桐平台账号
cp aics.local.example.json aics.local.json   # 各角色领证后回填AIC

# 3) 注册并领取证书（平台过审后）
source llm.env && source .acps.env
../.venv/Scripts/python.exe -u enroll_role.py statute advisor challenger arbiter persona researcher host

# 4) 一键启动（7个智能体 + AMP转发器）
./start_all.sh

# 5) 本地 Web 控制台（推荐）：浏览器里提问、看三阶段进度、读意见书
source llm.env && ../.venv/Scripts/python.exe webui.py    # 打开 http://127.0.0.1:9300

# 也可在梧桐平台"叮当"里 @会诊主持 使用
```

## Docker 一键部署（可选）

不想手动装 venv 时，用 Docker 起整套服务：七个智能体 + AMP 转发器 + WebUI **在同一个容器内**。

**为什么是一个容器**：七个角色之间用 `https://<role>.127-0-0-1.nip.io:910x` 互访，该域名解析到 `127.0.0.1`；拆成多个容器后各自解析到自己，伙伴端点会全部失联。因此队内伙伴端口只在容器内可见，对外只发布两个端口。

### 前置

1. Docker Desktop（或 Docker Engine + Compose v2）
2. 两个平台框架 wheel 放在仓库根目录（与手动安装相同）
3. 复制并填写配置：

```bash
cp agents/llm.env.example agents/llm.env                    # 填模型/检索密钥 + AGENT_TUNNEL
cp agents/aics.local.example.json agents/aics.local.json     # 领证后回填七个 AIC
```

`llm.env` 至少要填 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 和 `AGENT_TUNNEL`；后者缺失时容器会启动即报错退出并给出提示。

### 启动

```bash
docker compose up -d --build
docker compose ps          # STATUS 变成 healthy 即可
docker compose logs -f     # 三路日志分别带 [serve] / [forwarder] / [webui] 前缀
```

| 地址 | 用途 |
|---|---|
| http://127.0.0.1:9300 | WebUI 控制台 |
| 127.0.0.1:9101 | 会诊主持对外端点，cpolar 的 TCP 隧道指向这里 |
| 9102-9107 | 队内伙伴端口，仅容器内可达（按设计不发布） |

默认只绑定回环地址；需要同网段访问时用 `AIC_BIND_ADDR=0.0.0.0 docker compose up -d`。

### 停止与数据

```bash
docker compose down
```

容器**只读**挂载 `agents/llm.env`、`agents/aics.local.json`（密钥与身份不进镜像），**可写**挂载 `agents/workspaces`、`agents/consultations`、`agents/fluent-bit-state`、`logs`，`down` 之后数据仍留在宿主。

### 说明

- 未领证时 `serve` 会以 `blocked: acps_identity_not_active` 启动且不监听端口（与手动部署一致）；此时 WebUI 自动回落到本地直连模式，可直接体验会诊流程。
- 领证仍建议在宿主侧用 `enroll_role.py` 完成：证书写入 `agents/workspaces`，容器共享同一目录，重启容器即可生效。

## 两种运行模式

**作品的正式形态是平台互联模式**：七个智能体各自持有平台身份，经真实 AIP 协议跨进程互联（mTLS 双向认证），产生平台账本与排行榜数据——这是比赛要求的核心能力。**本地直连模式只是测试辅助**：为方便拉取仓库后快速体验界面与协作逻辑，同进程直调，不产生任何平台互联数据。

控制台会自动选择模式（页面右上角实时显示）：

| | 平台互联模式（正式） | 本地直连模式（仅测试） |

| | 平台互联模式 | 本地直连模式 |
|---|---|---|
| 触发条件 | 存在 `workspaces/host/acps` 身份（已完成注册领证） | 无平台身份，或设 `WEBUI_LOCAL=1` |
| 调用方式 | 真实 AIP 跨进程委派（mTLS 双向认证，产生平台账本/排行榜数据） | 同进程直调七个智能体 |
| 需要配置 | API 密钥 + 平台账号 + AIC 身份表 + 隧道 | **只需 llm.env 里的 API 密钥**（联网检索另需 `SEARCH_API_KEY`） |
| 适用场景 | 比赛/评测/演示真实互联 | 拉取仓库后快速体验界面与协作逻辑 |

只想到手就测：`cp llm.env.example llm.env` 填入任意 OpenAI 兼容端点与密钥（联网检索官再填一个博查 `SEARCH_API_KEY`），启动 webui 即进入本地直连模式，无需注册、无需证书、无需隧道。

## 目录结构

```
├── agents/                 # 七个智能体源码（一个文件=一个角色）
│   ├── rtbase.py           # 共享配置：平台接入/端点拓扑/Windows补丁/步数预算钩子
│   ├── serve.py            # 多角色服务进程（直连+群组双协议监听）
│   ├── enroll_role.py      # 注册状态机（领证）
│   ├── amp_forwarder.py    # AMP日志→平台计量上报（官方Fluent Bit链路的Windows等价实现）
│   ├── webui.py + webui_static/  # 本地 Web 控制台（三阶段进度+意见书+历史）
│   └── start_all.sh        # 一键启动
├── examples/               # 真实会诊案例存档（含跨队智能体调用记录）
└── README.md
```

## 匿名化说明

按参赛规则，仓库中的部署身份标识（各智能体 AIC、公网隧道地址、平台账号、API 密钥）均已替换为占位符或隔离在 gitignore 之外（`llm.env` / `.acps.env` / `aics.local.json`），线上注册版本含真实标识。

## License

MIT
