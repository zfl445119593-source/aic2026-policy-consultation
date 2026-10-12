"""智能体3/6 · 落地顾问

把条文语言翻译成具体对象可执行的清单：
你是谁(适用对象) → 要做什么 → 何时截止 → 备什么材料 → 在哪办理。
"""
import json
import re
from wit import AmpAccessLoggingHooks, WitAgent, AcpsDirect, capability, AcpsGroup
from rtbase import make_provider, make_acps_config, endpoint_for as rtbase_endpoint_for, group_admission_hooks, step_budget_hook

PORT = 9103

CHECKLIST_FIELDS = ["适用对象", "待办事项", "时间节点", "所需材料", "办理渠道", "依据条文"]


@capability(
    id="advisor.checklist-schema",
    description="校验/补全执行清单的六要素结构，缺项给出提示。",
    acps_skill=True,
    tags=["政策会诊", "执行清单"],
)
def checklist_schema(items_json: str) -> str:
    try:
        items = json.loads(items_json)
        if isinstance(items, dict):
            items = [items]
    except json.JSONDecodeError:
        return "输入不是合法JSON"
    report = []
    for i, it in enumerate(items, 1):
        missing = [f for f in CHECKLIST_FIELDS if not it.get(f)]
        report.append(f"事项{i}：{'完整' if not missing else '缺少 ' + '、'.join(missing)}")
    return "\n".join(report)


@capability(
    id="advisor.deadline-extract",
    description="从政策文本中提取时间节点表述（截止日/有效期/批次时间），标注是否明确。",
    acps_skill=True,
    tags=["政策会诊", "时间节点"],
)
def deadline_extract(text: str) -> str:
    pats = [
        (r"截止[日期时间]*[为至]?[^，。；\n]{2,20}", "截止类"),
        (r"\d{4}年\d{1,2}月\d{1,2}日[^，。；\n]{0,10}", "明确日期"),
        (r"每[年月][^，。；\n]{2,15}", "周期类"),
        (r"[自起]发布之日起[^，。；\n]{2,12}", "生效类"),
    ]
    found = []
    for pat, kind in pats:
        for m in re.findall(pat, text):
            found.append({"类型": kind, "表述": m.strip()})
    if not found:
        return json.dumps({"时间节点数": 0, "提示": "未提取到明确时间，清单中时间栏应标'需核实'"}, ensure_ascii=False)
    return json.dumps({"时间节点数": len(found), "清单": found}, ensure_ascii=False)


def build_agent(with_acps: bool = True):
    return WitAgent(
        id="policy-roundtable-advisor",
        display_name="政策落地执行顾问",
        description="【政策会诊团队成员（编排者：政策会诊主持）；会诊顺序：阶段一并行独立作答；亦可独立使用。】政策落地执行顾问：把政策条文翻译成'你要做什么'的六要素执行清单——谁（适用对象）、做什么（事项）、何时（时限/窗口期）、备什么材料、去哪办理、依据哪条。适合已确定适用某政策、需要办理路径和材料清单的用户（企业办事员/科研秘书/毕业生等）。",
        version="0.2.4",
        instructions=(
            "你是落地顾问，负责'翻译'：把条文语言变成具体的人能照着做的清单。\n"
            "阶段一（独立作答）：基于你掌握的政策知识给出执行清单，每个事项按六要素组织"
            "（适用对象/待办事项/时间节点/所需材料/办理渠道/依据条文），写完用 advisor.checklist-schema 自检；"
            "涉及时间的内容先用 advisor.deadline-extract 提取核对，提取不到的一律写'需核实'，不许猜日期。\n"
            "阶段二（交叉质询）：重点回应风险审查官对你清单'依据性'的挑战——"
            "你引用的条文若被条文核查官判定不合规，立即修正清单而不是辩解。\n"
            "文风：面向'具体的人'说话（科研秘书/企业办事员/毕业生），禁止公文腔；每句话都要能回答'所以我下一步干什么'。"
        ),
        model=make_provider("advisor"),
        capabilities=[checklist_schema, deadline_extract],
        amp_hooks=[AmpAccessLoggingHooks()],
        aip_hooks=[group_admission_hooks()] if with_acps else [],
        hooks=[step_budget_hook(24)] if with_acps else [],
        protocols=[AcpsDirect(), AcpsGroup()] if with_acps else [],
    )


if __name__ == "__main__":
    from wit import WitRuntime
    agent = build_agent()
    runtime = WitRuntime(
        agent,
        workspace="workspaces/advisor",
        acps=make_acps_config(rtbase_endpoint_for("advisor") + "/acps/aip/v1"),
    )
    path = runtime.save_acs()
    print(f"[落地顾问] ACS草稿 -> {path}")
    print(checklist_schema(json.dumps({"适用对象": "高校", "待办事项": "提交申报书"}, ensure_ascii=False)))
    print(deadline_extract("申报材料请于每年3月31日前提交，自发布之日起30日内生效。"))
