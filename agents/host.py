"""智能体1/6 · 会诊主持（Leader）

政策会诊系统的入口与编排者：
接收用户政策问题 → 澄清追问 → 创建群组拉入专家 → 组织三阶段流转
（独立作答 → 交叉质询 → 仲裁）→ 对外输出《会诊意见书》。
"""
import json
import re
from wit import AipDelegationHooks, AmpAccessLoggingHooks, WitAgent, AcpsDirect, capability, AcpsGroup
from rtbase import make_provider, make_acps_config, endpoint_for as rtbase_endpoint_for, step_budget_hook, group_admission_hooks, team_aic

PORT = 9101


@capability(
    id="consult.intake",
    description="解析政策咨询问题，提取领域、适用对象、时间要素与紧急度。",
    acps_skill=True,
    tags=["政策会诊", "需求解析"],
)
def intake(question: str) -> str:
    """确定性初筛（不依赖大模型）：抓取问题中的关键要素供主持参考。"""
    domains = ["税收", "社保", "科技申报", "教育", "人才", "补贴", "资质", "知识产权", "高新技术企业", "实验室"]
    found = [d for d in domains if d in question]
    dates = re.findall(r"\d{4}年|\d{1,2}月\d{1,2}日|年底|季度", question)
    subjects = [s for s in ["公司", "学校", "高校", "个体", "毕业生", "医院"] if s in question]
    return json.dumps({
        "领域线索": found,
        "对象线索": subjects,
        "时间线索": dates,
        "问题长度": len(question),
        "建议": "要素不足时应先向用户澄清，再建组会诊",
    }, ensure_ascii=False)


@capability(
    id="consult.agenda",
    description="生成标准会诊议程：三阶段、参与角色与各阶段产出物。",
    acps_skill=True,
    tags=["政策会诊", "会诊议程"],
)
def agenda(topic: str) -> str:
    return json.dumps({
        "会诊主题": topic,
        "阶段一_独立作答": {
            "参与": ["条文核查官", "落地顾问", "风险审查官"],
            "规则": "各自独立回答，互不可见，防止观点污染",
            "产出": "各专家结论+置信度+引用",
        },
        "阶段二_交叉质询": {
            "参与": ["全部专家", "适用对象代理"],
            "规则": "风险审查官主导挑刺，对象代理代表用户追问，条文核查官举证",
            "产出": "质询记录+被击破/被捍卫的结论清单",
        },
        "阶段三_仲裁": {
            "参与": ["仲裁官"],
            "规则": "汇总分歧，量化置信度，不强行拍板",
            "产出": "共识结论+分歧标注+核实建议",
        },
    }, ensure_ascii=False)


@capability(
    id="consult.opinion-format",
    description="把会诊结论渲染为标准《会诊意见书》三段式结构。",
    acps_skill=True,
    tags=["政策会诊", "意见书"],
)
def opinion_format(consensus: str, dissent: str, todo: str) -> str:
    return (
        "【会诊意见书】\n"
        f"一、共识结论\n{consensus}\n\n"
        f"二、分歧与不确定项（建议向发文机关核实）\n{dissent}\n\n"
        f"三、建议执行清单\n{todo}"
    )


def build_agent(with_acps: bool = True):
    return WitAgent(
        id="policy-roundtable-host",
        display_name="政策会诊主持",
        # v0.2.2/0.2.3 更新连续 PENDING（疑似审核敏感词），弃更——保持与已过审 v0.2.1 记录一致
        description="政策会诊团队编排者与系统入口：先澄清需求，再按固定顺序组织三阶段会诊——阶段一：条文核查官、落地顾问、风险审查官三位并行独立作答；阶段二：风险审查官主导交叉质询（条文官举证、用户代言人追问）；阶段三：仲裁官汇总裁决——最终输出《会诊意见书》。请把政策问题直接交给我，由我编排团队完成会诊。",
        version="0.2.4",
        instructions=(
            "你是政策会诊主持（Leader），是唯一的对外入口。工作流程：\n"
            "1) 接到问题先用 consult.intake 初筛：领域/对象/时间三类要素缺任何一类，先向用户澄清，不要急着建组。\n"
            "2) 要素齐全后，用 consult.agenda 生成议程，直接按下述固定名单委派专家（aip_delegate，direct-rpc）。"
            "注意：不要用发现服务搜索本队专家（acps_discover 对本队无效），名单如下：\n"
            f"   - 条文核查官 AIC: {team_aic('statute')}\n"
            f"   - 落地顾问 AIC: {team_aic('advisor')}\n"
            f"   - 风险审查官 AIC: {team_aic('challenger')}\n"
            f"   - 仲裁官 AIC: {team_aic('arbiter')}\n"
            f"   - 用户代言人 AIC: {team_aic('persona')}\n"
            "3) 严格按三阶段推进：阶段一让三位专家独立作答（此阶段不得互相看到答案）；阶段二组织交叉质询（由风险审查官主导）；阶段三交仲裁官汇总。\n"
            "4) 拿到仲裁结果后，用 consult.opinion-format 生成《会诊意见书》交付用户。\n"
            "纪律：你自己不下专业结论、不代替专家回答；对用户始终用通俗语言；会诊过程对用户可见摘要、专家原文按需展开；"
            "委派失败时对同一专家最多重试1次，仍失败则在意见书中如实标注该专家缺席，不中断整个会诊。\n"
            "5) 意见书末尾必须有\"外部智能体调用记录\"段：逐条列出本场会诊委派过的所有平台外部（非本队）智能体——名字、AIC尾4位、调用目的、结果；本队成员（含检索官）的委派在过程摘要中说明，不进该段；一场都没调用则写\"本场会诊未调用外部智能体\"。"
        ),
        model=make_provider("host"),
        capabilities=[intake, agenda, opinion_format],
        amp_hooks=[AmpAccessLoggingHooks()],
        aip_hooks=[group_admission_hooks()] if with_acps else [],
        hooks=[AipDelegationHooks(delivery="rpc"), step_budget_hook(40)] if with_acps else [],
        protocols=[AcpsDirect(), AcpsGroup()] if with_acps else [],
    )


if __name__ == "__main__":
    from wit import WitRuntime
    agent = build_agent()
    runtime = WitRuntime(
        agent,
        workspace="workspaces/host",
        acps=make_acps_config(rtbase_endpoint_for("host") + "/acps/aip/v1"),
    )
    path = runtime.save_acs()
    print(f"[会诊主持] ACS草稿 -> {path}")
    print(intake("我们高校想明年申报重点实验室，科技申报的截止时间一般是什么时候？"))
