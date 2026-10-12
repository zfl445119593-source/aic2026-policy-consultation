"""智能体5/6 · 仲裁官

会诊的最后环节：汇总各专家结论与质询记录，
量化分歧 → 给出置信度 → 产出三段式《会诊意见书》素材。
建议配置最强模型（结论质量的天花板）。
"""
import json
from statistics import mean
from wit import AmpAccessLoggingHooks, WitAgent, AcpsDirect, capability, AcpsGroup
from rtbase import make_provider, make_acps_config, endpoint_for as rtbase_endpoint_for, group_admission_hooks, step_budget_hook

PORT = 9105


@capability(
    id="arbiter.confidence-schema",
    description="汇总专家结论与置信度，计算共识度并标记分歧点。",
    acps_skill=True,
    tags=["政策会诊", "仲裁"],
)
def confidence_schema(answers_json: str) -> str:
    """answers: [{"expert","conclusion","confidence"(0-1),"citation_ok"(bool)}]"""
    try:
        answers = json.loads(answers_json)
    except json.JSONDecodeError:
        return "输入不是合法JSON"
    confs = [a.get("confidence", 0) for a in answers]
    avg = round(mean(confs), 2) if confs else 0
    spread = round(max(confs) - min(confs), 2) if confs else 0
    no_cite = [a["expert"] for a in answers if not a.get("citation_ok")]
    verdict = "高共识" if spread <= 0.2 and avg >= 0.7 else ("存在分歧" if spread > 0.3 else "中等共识")
    return json.dumps({
        "共识度": verdict,
        "平均置信度": avg,
        "置信度差": spread,
        "缺引用支撑的专家": no_cite or "无",
        "处理建议": "分歧项写入意见书第二段，建议向发文机关核实" if verdict != "高共识" else "可写入共识结论",
    }, ensure_ascii=False)


@capability(
    id="arbiter.verdict-rules",
    description="返回仲裁处置规则表：不同共识度/质询结果对应的意见书写法。",
    acps_skill=True,
    tags=["政策会诊", "仲裁规则"],
)
def verdict_rules() -> str:
    return json.dumps([
        {"情形": "高共识+质询未被击破", "写法": "写入共识结论，附全部引用"},
        {"情形": "高共识但缺引用", "写法": "降级为'待核实'，写入第二段"},
        {"情形": "存在分歧", "写法": "双方结论+理由并列写入分歧段，不拍板"},
        {"情形": "结论被质询击破", "写法": "剔除出共识，记录击破理由"},
        {"情形": "涉及地方细则而未核实", "写法": "标注'以当地主管部门口径为准'"},
    ], ensure_ascii=False)


def build_agent(with_acps: bool = True):
    return WitAgent(
        id="policy-roundtable-arbiter",
        display_name="政策会诊仲裁官",
        description="【政策会诊团队成员（编排者：政策会诊主持）；会诊顺序：阶段三汇总裁决（终审）；亦可独立使用。】政策会诊仲裁员：汇总多位专家的结论与置信度，计算共识度，明确区分'专家一致认同的结论'与'存在分歧、需向官方核实的结论'，按处置规则产出最终意见：共识结论/分歧标注/核实建议三段式。不做和稀泥式折中——分歧就是分歧。",
        version="0.2.4",
        instructions=(
            "你是仲裁官，只在阶段三出场，职责是'汇总'不是'独裁'。\n"
            "输入：各专家独立作答（含置信度与引用）+ 质询记录（谁被击破/被捍卫）。\n"
            "流程：\n"
            "1) 把各专家结论整理成统一格式后调用 arbiter.confidence-schema 计算共识度；\n"
            "2) 严格按 arbiter.verdict-rules 的处置表决定每条结论的去向（共识段/分歧段/剔除）；\n"
            "3) 输出三段式素材：①共识结论（带引用）②分歧与不确定项（双方理由并列，不拍板）③建议执行清单素材。\n"
            "纪律：缺引用的结论一律降级'待核实'；被质询击破的结论不得进入共识段；"
            "你不新增任何事实性内容，只做汇总、降级与标注。"
        ),
        model=make_provider("arbiter"),
        capabilities=[confidence_schema, verdict_rules],
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
        workspace="workspaces/arbiter",
        acps=make_acps_config(rtbase_endpoint_for("arbiter") + "/acps/aip/v1"),
    )
    path = runtime.save_acs()
    print(f"[仲裁官] ACS草稿 -> {path}")
    demo = json.dumps([
        {"expert": "条文核查官", "conclusion": "申报截止为每年3月底", "confidence": 0.9, "citation_ok": True},
        {"expert": "落地顾问", "conclusion": "截止约为3月底，但需按当年通知", "confidence": 0.6, "citation_ok": False},
    ], ensure_ascii=False)
    print(confidence_schema(demo))
