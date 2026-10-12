"""智能体6/6 · 适用对象代理（选做角色）

扮演具体政策适用对象（如"高校科研秘书""小微企业主""应届毕业生"），
用大白话追问专家——让会诊里有真实的"问方"，把专家结论拉回用户视角。
"""
import json
import random
from wit import AmpAccessLoggingHooks, WitAgent, AcpsDirect, capability, AcpsGroup
from rtbase import make_provider, make_acps_config, endpoint_for as rtbase_endpoint_for, group_admission_hooks, step_budget_hook

PORT = 9106

PERSONAS = ["高校科研秘书", "小微企业主", "应届毕业生", "个体工商户", "乡镇经办人员", "医院科研科干事"]


@capability(
    id="persona.clarify-questions",
    description="以指定身份视角生成3个最关心的追问，用于质询环节。",
    acps_skill=True,
    tags=["政策会诊", "用户视角"],
)
def clarify_questions(role: str, topic: str) -> str:
    extras = [
        f"{topic}对我们这类对象有没有特殊通道或豁免？",
        "错过时间还能补救吗？",
        "办不成最常见的原因是什么？",
    ]
    # 首问固定带身份，保证"以XX身份"的视角明确；第三问按场景轮换
    picks = [f"我作为{role}，第一步该干什么？", "要花多少钱/准备多久？", random.choice(extras)]
    return "\n".join(f"· {q}" for q in picks)


@capability(
    id="persona.profile",
    description="返回可用身份库及各身份最关心的政策要素，供主持选择扮演对象。",
    acps_skill=True,
    tags=["政策会诊", "身份库"],
)
def profile() -> str:
    return json.dumps({
        "高校科研秘书": ["申报窗口期", "经费配套要求", "平台考核指标"],
        "小微企业主": ["税费减免门槛", "补贴到账周期", "申报被拒常见原因"],
        "应届毕业生": ["落户/补贴申请条件", "材料清单", "办理时限"],
        "个体工商户": ["减免适用范围", "年审要求", "跨区经营口径"],
        "乡镇经办人员": ["代办权限边界", "群众常见误区", "上级检查要点"],
    }, ensure_ascii=False)


def build_agent(with_acps: bool = True):
    return WitAgent(
        id="policy-roundtable-persona",
        display_name="政策用户代言人",
        description="【政策会诊团队成员（编排者：政策会诊主持）；会诊顺序：阶段二交叉质询中代表用户追问；亦可独立使用。】政策适用对象代言人：以真实用户身份（小微企业主/高校科研秘书/应届毕业生等）替你追问专家最关心的问题——第一步该干什么、要花多少钱、办不成最常见的原因。确保会诊结论说人话、可落地。",
        version="0.2.4",
        instructions=(
            "你是适用对象代理，在阶段二代表'真实用户'出场。\n"
            "1) 会诊开始时用 persona.profile 查身份库，根据问题选定最贴切的身份并声明'本次以XX身份提问'；\n"
            "2) 用 persona.clarify-questions 生成该身份最关心的追问，再结合专家的具体结论补充追问；\n"
            "3) 只问'这个身份真会问的问题'（要花钱吗、要多久、办不成怎么办、和我这类人有关吗），不问学术性问题；\n"
            "4) 专家回答后若仍有疑惑，换更直白的话再问一次，直到答案'人话'为止；\n"
            "5) 会诊结束前给一句'用户视角总结'：专家们说的这事，对我们意味着什么。"
        ),
        model=make_provider("persona"),
        capabilities=[clarify_questions, profile],
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
        workspace="workspaces/persona",
        acps=make_acps_config(rtbase_endpoint_for("persona") + "/acps/aip/v1"),
    )
    path = runtime.save_acs()
    print(f"[适用对象代理] ACS草稿 -> {path}")
    print(clarify_questions("高校科研秘书", "重点实验室申报"))
