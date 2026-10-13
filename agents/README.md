# 政策会诊 · 智能体源码（一个文件 = 一个智能体）

| 文件 | 角色 | 端口 | 职责一句话 |
|---|---|---|---|
| rtbase.py | 共享配置 | - | 模型接入/平台ACPs配置/端点拓扑/Windows补丁/步数预算钩子/AIC身份表读取 |
| host.py | 会诊主持(Leader) | 9101 | 唯一入口：澄清、编排三阶段、交付意见书 |
| statute.py | 条文核查官 | 9102 | 条文级引用+来源档位，事实底座；可委派检索官 |
| advisor.py | 落地顾问 | 9103 | 条文→执行清单六要素 |
| challenger.py | 风险审查官 | 9104 | 六维风险质询（时效/地域/例外/门槛/误读/冲突） |
| arbiter.py | 仲裁官 | 9105 | 分歧量化+置信度+三段式意见书 |
| persona.py | 用户代言人 | 9106 | 扮演具体身份替用户追问 |
| researcher.py | 政策原文检索官 | 9107 | 联网检索+政府网站原文抓取+权威度分级 |

运行支撑：

| 文件 | 用途 |
|---|---|
| serve.py | 多角色服务进程：直连(mTLS JSON-RPC)+群组(AMQP)双协议监听 |
| enroll_role.py | 注册状态机：对接过审记录并领取证书 |
| amp_forwarder.py | AMP 账本（心跳/访问/系统）→ 平台 Kafka 上报：官方 Fluent Bit 链路的 Windows 等价实现 |
| webui.py + webui_static/ | 本地 Web 控制台：浏览器发起会诊，三阶段进度可视、意见书渲染、历史查阅（`source llm.env && ../.venv/Scripts/python.exe webui.py` → http://127.0.0.1:9300） |
| start_all.sh | 一键启动（幂等：已在跑的进程跳过） |

## 启动

```bash
source llm.env && source .acps.env
./start_all.sh          # 或单独: ../.venv/Scripts/python.exe serve.py host
```

详见仓库根 README《快速开始》。

## 说明

- 每个文件里的 @capability 是**确定性能力**（不依赖大模型即可测试），大模型行为写在 instructions。
- 部署身份隔离：真实 AIC 写在 `aics.local.json`（模板 `aics.local.example.json`），
  公网隧道地址写在 `llm.env` 的 `AGENT_TUNNEL`，均不入库。
- workspaces/ 存放各角色证书、AMP账本、任务库（git忽略，内含私钥）。
