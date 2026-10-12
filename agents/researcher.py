"""智能体7 · 政策原文检索官

政策会诊团队的"弹药库"：受条文核查官（或主持）委派，把检索任务单转成搜索词，
联网查找政策文件原文，抓取网页提取条款级摘录，交付带来源三要素（原文摘录/来源
网址/发布日期）的检索报告。查不到就如实说查不到，绝不编造条款。
"""
import json
import re
import ssl
import urllib.request

from wit import AmpAccessLoggingHooks, WitAgent, AcpsDirect, AcpsGroup, capability
from rtbase import (make_provider, make_acps_config, endpoint_for as rtbase_endpoint_for,
                    group_admission_hooks, step_budget_hook)

PORT = 9107
SEARCH_API = "https://api.bochaai.com/v1/web-search"


def _search_key() -> str:
    import os
    return os.environ.get("SEARCH_API_KEY", "")


@capability(
    id="research.web-search",
    description="联网搜索政策文件/官方网页：返回标题、摘要、网址、发布时间。用于定位政策原文所在页面。",
    acps_skill=True,
    tags=["政策会诊", "联网检索", "原文定位"],
)
def web_search(query: str) -> str:
    if not _search_key():
        return json.dumps({"错误": "未配置 SEARCH_API_KEY"}, ensure_ascii=False)
    req = urllib.request.Request(
        SEARCH_API,
        data=json.dumps({"query": query, "summary": True, "count": 6}).encode(),
        headers={"Authorization": "Bearer " + _search_key(), "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            d = json.loads(r.read())
    except Exception as e:
        return json.dumps({"错误": f"搜索失败: {type(e).__name__}"}, ensure_ascii=False)
    pages = (d.get("data") or {}).get("webPages", {}).get("value", [])
    out = [
        {
            "标题": p.get("name", ""),
            "网址": p.get("url", ""),
            "摘要": (p.get("summary") or p.get("snippet") or "")[:200],
            "发布时间": p.get("datePublished", "未知"),
        }
        for p in pages[:6]
    ]
    return json.dumps({"结果数": len(out), "结果": out}, ensure_ascii=False, indent=1)


@capability(
    id="research.fetch-page",
    description="抓取指定网址的网页正文（已去HTML标签），供比对政策条款原文。优先抓政府/高校官网页面。",
    acps_skill=True,
    tags=["政策会诊", "原文抓取"],
)
def fetch_page(url: str) -> str:
    if not re.match(r"^https?://", url):
        return json.dumps({"错误": "仅支持 http/https 网址"}, ensure_ascii=False)
    ctx = ssl.create_default_context()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            raw = r.read(300_000)
    except ssl.SSLError:
        ctx = ssl._create_unverified_context()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
                raw = r.read(300_000)
        except Exception as e:
            return json.dumps({"错误": f"抓取失败: {type(e).__name__}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"错误": f"抓取失败: {type(e).__name__}: {str(e)[:80]}"}, ensure_ascii=False)
    for enc in ("utf-8", "gbk", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="ignore")
    text = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return json.dumps({"网址": url, "正文前6000字": text[:6000]}, ensure_ascii=False)


def build_agent(with_acps: bool = True) -> WitAgent:
    return WitAgent(
        id="policy-roundtable-researcher",
        display_name="政策原文检索官",
        description=(
            "【政策会诊团队成员（编排者：政策会诊主持）；通常受条文核查官委派执行原文检索，"
            "亦可独立使用。】政策原文检索员：联网查找政策文件的官方原文页面，抓取正文并提取"
            "条款级摘录，交付带来源三要素（原文摘录/来源网址/发布日期）的检索报告。"
            "查不到就如实报告查不到，绝不编造文件名、条款号或内容。"
            "典型任务：查找《南京市重点实验室建设与运行管理办法》现行版原文、"
            "检索某年度市级科技计划申报通知、定位某政策条款的官方出处。"
        ),
        version="0.1.0",
        instructions=(
            "你是政策原文检索官，政策会诊团队的弹药库。\n"
            "收到检索任务单（来自条文核查官、主持或用户）后：\n"
            "1) 把任务单拆解成2-4个具体搜索词（文件全名+发文机关+年份是最有效的组合）；\n"
            "2) 调 research.web-search 逐个搜索；优先选取 gov.cn / 省市政府 / 高校科技处 / 官方数据库的"
            "结果，标记来源权威度（官方原文/官方转载/第三方转述）；\n"
            "3) 对最可信的1-2个来源调 research.fetch-page 抓正文，从中提取与任务单直接相关的"
            "条款级原文摘录；\n"
            "4) 输出检索报告：每条包含【原文摘录｜来源网址｜发布日期｜权威度】，并明确列出"
            "哪些任务单项已找到、哪些经搜索未找到。\n"
            "纪律：只交付网页里真实存在的文字，摘录不得改写；搜索与抓取都失败时如实报告失败原因；"
            "绝不推断或补写任何条款内容；正文太长时优先摘录'第X条'附近的段落。"
        ),
        model=make_provider("researcher"),
        capabilities=[web_search, fetch_page],
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
        workspace="workspaces/researcher",
        acps=make_acps_config(rtbase_endpoint_for("researcher") + "/acps/aip/v1"),
    )
    path = runtime.save_acs()
    print(f"[检索官] ACS草稿 -> {path}")
    print(web_search("南京市重点实验室建设与运行管理办法"))
