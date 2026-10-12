"""智能体4/6 · 风险审查官

会诊里的"挑刺者"：适用例外、地方差异、文件时效、常见误读。
交叉质询环节的主要发起者——没有质疑就没有会诊价值。
"""
import json
from wit import AmpAccessLoggingHooks, WitAgent, AcpsDirect, capability, AcpsGroup
from rtbase import make_provider, make_acps_config, endpoint_for as rtbase_endpoint_for, group_admission_hooks, step_budget_hook

PORT = 9104

RISK_DIMENSIONS = [
    "时效性：文件是否过期/被修订/被替代",
    "地域性：全国性文件与本地细则的差异",
    "对象例外：谁不适用（事业单位/个体/外资等）",
    "条件门槛：数量/资质/信用等前置条件",
    "常见误读：字面理解与实际执行的偏差",
    "衔接冲突：与其他在行文件的冲突",
]


@capability(
    id="challenger.risk-dimensions",
    description="返回政策风险审查的标准六维清单，质询时逐维检查。",
    acps_skill=True,
    tags=["政策会诊", "风险质询"],
)
def risk_dimensions() -> str:
    return "\n".join(f"{i}. {d}" for i, d in enumerate(RISK_DIMENSIONS, 1))


@capability(
    id="challenger.challenge-form",
    description="按维度生成结构化质询单：针对某条结论输出质疑点、理由与要求对方提供的证据。",
    acps_skill=True,
    tags=["政策会诊", "质询单"],
)
def challenge_form(dimension: str, claim: str) -> str:
    dim_ok = any(dimension in d for d in RISK_DIMENSIONS)
    return json.dumps({
        "质询维度": dimension if dim_ok else f"{dimension}(非标准维度，建议对照六维清单)",
        "被质询结论": claim,
        "质疑点": f"该结论在'{dimension}'维度上是否成立未经验证",
        "要求证据": "请条文核查官出示原文引用，或由落地顾问说明实际执行口径",
        "处理": "举证成功则结论保留；举证失败则标记'存疑'并转仲裁官",
    }, ensure_ascii=False)


def build_agent(with_acps: bool = True):
    return WitAgent(
        id="policy-roundtable-challenger",
        display_name="政策风险审查官",
        description="【政策会诊团队成员（编排者：政策会诊主持）；会诊顺序：阶段一并行独立作答、阶段二主导交叉质询；亦可独立使用。】政策风险审查员：专挑政策解读中的坑，按六个维度逐项审查——时效（政策是否过期/未生效）、地域（全国性还是地方性）、例外条款、申报门槛、常见误读（数字/口径/主体搞错）、新旧政策冲突。给它一段政策结论，它输出质询单：哪里可疑、为什么、需要补什么证据。",
        version="0.2.4",
        instructions=(
            "你是风险审查官，立场是'默认每条结论都有坑'。\n"
            "阶段一（独立作答）：先用 challenger.risk-dimensions 调出六维清单，"
            "对问题涉及的领域逐维给出风险初判（每维一句话+高/中/低风险），不确定的维度标'待查证'。\n"
            "阶段二（交叉质询·你的主场）：用 challenger.challenge-form 生成质询单，逐条挑战其他专家的结论，重点打击：\n"
            "  - 无引用支撑的结论；\n"
            "  - 用全国文件回答地方问题（或反之）；\n"
            "  - 引用了过期/被修订文件的结论。\n"
            "每个质疑给出'为什么'和'怎么核实'，不为反对而反对。\n"
            "阶段三：不参与仲裁，但接受仲裁官对你质询记录的引用。"
        ),
        model=make_provider("challenger"),
        capabilities=[risk_dimensions, challenge_form],
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
        workspace="workspaces/challenger",
        acps=make_acps_config(rtbase_endpoint_for("challenger") + "/acps/aip/v1"),
    )
    path = runtime.save_acs()
    print(f"[风险审查官] ACS草稿 -> {path}")
    print(risk_dimensions())
    print(challenge_form("时效性", "高企认定研发费用占比要求为60%"))
