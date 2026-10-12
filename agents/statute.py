"""智能体2/6 · 条文核查官

负责政策原文与条文级引用：会诊中一切结论的"事实底座"。
可调用平台外部检索智能体（研伴·文献检索/新闻检索/时间线）作为弹药源。
"""
import json
import re
from wit import WitAgent, AcpsGroup, AcpsDirect, capability, AipDelegationHooks, AmpAccessLoggingHooks
from rtbase import make_provider, make_acps_config, endpoint_for as rtbase_endpoint_for, group_admission_hooks, step_budget_hook, team_aic as _team_aic

PORT = 9102


def _check_one(citation: str) -> dict:
    m = re.match(r"^《([^》]+)》\s*(.*)$", citation.strip())
    if not m:
        return {"引用": citation, "判定": "不合规", "原因": "缺少《文件名》"}
    doc, loc = m.groups()
    if re.search(r"第[一二三四五六七八九十百\d]+[条款项章]", loc or ""):
        return {"引用": citation, "判定": "合规", "文件": doc, "条款": loc.strip()}
    return {"引用": citation, "判定": "部分合规", "原因": f"《{doc}》缺条款定位，建议精确到条/款"}


@capability(
    id="statute.cite-check",
    description="校验并规范化条文引用：必须是《文件名》+条款定位，能定位到条/款/项。",
    acps_skill=True,
    tags=["政策会诊", "条文引用"],
)
def cite_check(citation: str) -> str:
    r = _check_one(citation)
    if "原因" in r:
        return r["判定"] + "：" + r["原因"]
    return r["判定"] + f"：《{r.get('文件', '')}》{r.get('条款', '')} 定位完整"


@capability(
    id="statute.citation-extract",
    description="从一段文本中抽取全部条文引用并逐条校验合规性，返回结构化清单。",
    acps_skill=True,
    tags=["政策会诊", "引用抽取"],
)
def citation_extract(text: str) -> str:
    cites = re.findall(r"《[^》]+》[^，。；\n]{0,20}", text)
    if not cites:
        return json.dumps({"引用数": 0, "警示": "全文无条文引用，结论缺乏依据，质询阶段应被挑战"}, ensure_ascii=False)
    return json.dumps({"引用数": len(cites), "清单": [_check_one(c) for c in cites]}, ensure_ascii=False)


def build_agent(with_acps: bool = True):
    return WitAgent(
        id="policy-roundtable-statute",
        display_name="政策条文核查官",
        description="【政策会诊团队成员（编排者：政策会诊主持）；会诊顺序：阶段一并行独立作答、阶段二交叉质询中负责条文举证；亦可独立使用。】政策条文核查员：专门回答'政策原文到底怎么规定的'。给出条文级引用（《文件名》第X条第X款第X项）、标注依据强度（原文明确/需推断/查无原文），并自检引用格式。典型问题：高新技术企业认定的科技人员占比要求依据哪个条款、原文如何表述。不做主观解读，只提供可查证的事实。",
        version="0.2.4",
        instructions=(
            "你是条文核查官，会诊的'事实底座'。\n"
            "阶段一（独立作答）：围绕问题给出基于政策原文的结论。每条结论必须：\n"
            "  a) 标注来源档位：【原文明确】【需推断】【查无原文】三选一；\n"
            "  b) 引用格式为《文件名》第X条第X款，写完用 statute.cite-check 或 statute.citation-extract 自检；\n"
            "  c) 给出置信度(0-1)。查无原文时如实说，绝不编造文件名和条款号。\n"
            "阶段二（交叉质询）：只回应有引用支撑的质疑；被要求举证时出示原文或承认【需推断】；"
            "发现他人引用不合规，立即用 citation-extract 指出。\n"
            f"需要政策原文而你手头没有时，优先委派本队检索官（政策原文检索官 AIC: {_team_aic('researcher')}，aip_delegate direct-rpc）执行检索任务单：写明文件全名/发文机关/需要的条款主题，拿到它的检索报告（原文摘录+来源网址+发布日期）后据此作答，并注明'已经外部检索核实'。自家检索官不可用时才考虑平台其他检索类智能体，再不可用则如实标'待核实'。"
        ),
        model=make_provider("statute"),
        capabilities=[cite_check, citation_extract],
        amp_hooks=[AmpAccessLoggingHooks()],
        aip_hooks=[group_admission_hooks()] if with_acps else [],
        hooks=[AipDelegationHooks(delivery="rpc"), step_budget_hook(24)] if with_acps else [],
        protocols=[AcpsDirect(), AcpsGroup()] if with_acps else [],
    )


if __name__ == "__main__":
    from wit import WitRuntime
    agent = build_agent()
    runtime = WitRuntime(
        agent,
        workspace="workspaces/statute",
        acps=make_acps_config(rtbase_endpoint_for("statute") + "/acps/aip/v1"),
    )
    path = runtime.save_acs()
    print(f"[条文核查官] ACS草稿 -> {path}")
    print(cite_check("《XX省科技计划管理办法》第五条"))
    print(citation_extract("依据《高新技术企业认定管理办法》第十一条和《税收征管法》，可享受优惠。"))
