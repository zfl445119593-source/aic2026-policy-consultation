"""政策会诊 · 本地 Web 控制台（仅标准库，无第三方依赖）

浏览器里提问 → 以主持身份真实 AIP 委派本地七个智能体（mTLS）→ 三阶段进度可视 → 《会诊意见书》

用法（Git Bash，agents 目录）:
    source llm.env && ../.venv/Scripts/python.exe webui.py
    打开 http://127.0.0.1:9300
前提: serve.py 七角色已运行（./start_all.sh）；主持已注册领证（workspaces/host/acps 存在）

设计要点:
    - 两种模式自动选择（health/startup 会标明）:
        * 平台互联模式: 存在 workspaces/host/acps 身份时启用——真实 AIP 委派(mTLS)，
          引擎用独立工作区 workspaces/webui_host 避免与常驻 serve 双写冲突。
        * 本地直连模式: 无平台身份（或 WEBUI_LOCAL=1）时启用——同进程直调七个智能体，
          只需 llm.env 的 API 密钥，无需注册/证书/隧道；联网检索需 SEARCH_API_KEY。
    - 与 engine_platform.py 同构的三阶段编排，增加用户代言人追问与进度事件。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import threading
import time
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
AGENTS_DIR = Path(__file__).resolve().parent
os.chdir(AGENTS_DIR)

import rtbase
from wit import AgentState, RunConfig, TextPart, WitRuntime

PORT = int(os.environ.get("WEBUI_PORT", "9300"))
STATIC_DIR = AGENTS_DIR / "webui_static"
HOST_WS = Path("workspaces/host")
ENGINE_WS = Path("workspaces/webui_host")

ROLE_NAMES_UI = {"host": "会诊主持", "statute": "条文核查官", "advisor": "落地顾问",
                 "challenger": "风险审查官", "arbiter": "仲裁官", "persona": "用户代言人",
                 "researcher": "政策原文检索官"}
EXPERTS = ["statute", "advisor", "challenger"]
TERMINAL = {"awaiting-input", "awaiting-completion", "completed", "failed", "rejected", "canceled"}


def ensure_engine_workspace() -> None:
    """引擎独立工作区：复制主持 acps（身份证书+发现缓存）。主持重新领证后自动刷新。"""
    src, dst = HOST_WS / "acps", ENGINE_WS / "acps"
    if not src.exists():
        raise SystemExit("未找到 workspaces/host/acps —— 请先完成注册领证: enroll_role.py host")
    src_newest = max(p.stat().st_mtime for p in src.rglob("*") if p.is_file())
    dst_newest = max((p.stat().st_mtime for p in dst.rglob("*") if p.is_file()), default=0) if dst.exists() else 0
    if src_newest > dst_newest:
        if ENGINE_WS.exists():
            shutil.rmtree(ENGINE_WS)
        ENGINE_WS.mkdir(parents=True)
        shutil.copytree(src, dst)


def _prod_text(p) -> str:
    """拆产物信封：兼容 text/content 顶层字段与 data_items 列表两层结构。"""
    if isinstance(p, dict):
        if p.get("text") or p.get("content"):
            return str(p.get("text") or p.get("content"))
        for d in p.get("data_items") or []:
            if isinstance(d, dict) and (d.get("text") or d.get("content")):
                return str(d.get("text") or d.get("content"))
    return str(p)


def platform_identity_ready() -> bool:
    """是否已真正领证（具备可用的平台身份）。

    判据是"证书材料是否落盘"，而不是"workspaces/host/acps 目录是否存在"。
    因为 ACS 草稿（acs.draft.json）与候选 ACS（acs.candidate.json）也写在这个目录里，
    只看目录会让"生成过草稿、尚未领证"的机器被误判为平台互联模式，
    本地直连测试随即失败（实测：AipError: Partner has no discovered Direct JSON-RPC endpoint）。
    证书材料按后缀识别（.pem/.crt/.key），不依赖 SDK 内部的具体文件名。
    """
    acps_dir = HOST_WS / "acps"
    if not acps_dir.is_dir():
        return False
    return any(p.suffix.lower() in {".pem", ".crt", ".key"}
               for p in acps_dir.rglob("*") if p.is_file())


def choose_mode() -> str:
    return "platform" if platform_identity_ready() and os.environ.get("WEBUI_LOCAL") != "1" else "local"


def local_role_ready(role: str) -> bool:
    ok = rtbase.llm_ready(role)
    if role == "researcher":
        ok = ok and bool(os.environ.get("SEARCH_API_KEY"))
    return ok


class ConsultEngine:
    """三阶段会诊（真实 AIP 委派）。on_event 回调用于向 Web 前端推送进度。"""

    def __init__(self, on_event):
        self.on_event = on_event

    def _emit(self, kind: str, **kw) -> None:
        self.on_event({"kind": kind, "ts": round(time.time(), 1), **kw})

    async def _ask(self, runtime: WitRuntime, role: str, prompt: str, timeout_s: int = 300) -> str:
        t0 = time.time()
        self._emit("delegate.start", role=role, name=ROLE_NAMES_UI.get(role, role))
        aic = rtbase.team_aic(role)
        state = AgentState()
        task = await runtime.aip_delegate(state, partner_aic=aic, request=[TextPart(prompt)])
        ref = task["task_ref"]
        waited = 0.0
        while task.get("state") not in TERMINAL:
            task = await runtime.aip_task(state, action="wait", task_ref=ref)
            waited += 5
            if waited > timeout_s:
                await runtime.aip_task(state, action="cancel", task_ref=ref)
                raise TimeoutError(f"{role} 委派超时({timeout_s}s)")
        if task.get("state") == "awaiting-completion":
            task = await runtime.aip_task(state, action="complete", task_ref=ref)
        products = task.get("products") or task.get("product") or []
        texts = [_prod_text(p) for p in (products if isinstance(products, list) else [products])]
        out = "\n".join(t for t in texts if t and t != "None") or json.dumps(task, ensure_ascii=False, default=str)[:1500]
        self._emit("delegate.done", role=role, name=ROLE_NAMES_UI.get(role, role),
                   chars=len(out), seconds=round(time.time() - t0, 1))
        return out

    async def _ask_local(self, role: str, prompt: str, max_steps: int = 16, retries: int = 2) -> str:
        """本地直连模式：同进程直调该角色（无身份/无网络互联），带空响应退避重试。"""
        t0 = time.time()
        self._emit("delegate.start", role=role, name=ROLE_NAMES_UI.get(role, role))
        module = __import__(role)
        text = ""
        for attempt in range(retries + 1):
            agent = module.build_agent(with_acps=False)
            provider = agent.model
            runtime = WitRuntime(agent)
            try:
                result = await runtime.run(prompt, RunConfig(max_steps=max_steps))
                text = getattr(result, "final_text", "") or ""
                if getattr(result, "status", "") == "budget_exhausted":
                    self._emit("delegate.error", role=role, name=ROLE_NAMES_UI.get(role, role),
                               error=f"步数预算耗尽(重试{attempt + 1})")
                    text = ""
            except Exception as e:  # noqa: BLE001
                self._emit("delegate.error", role=role, name=ROLE_NAMES_UI.get(role, role),
                           error=f"{type(e).__name__}: {str(e)[:120]} (重试{attempt + 1})")
                text = ""
            finally:
                if provider is not None:
                    await provider.aclose()
            if len(text.strip()) >= 30:
                break
            await asyncio.sleep(2 * (attempt + 1))
        self._emit("delegate.done", role=role, name=ROLE_NAMES_UI.get(role, role),
                   chars=len(text), seconds=round(time.time() - t0, 1))
        return text

    async def run(self, question: str) -> dict:
        self.mode = choose_mode()
        if self.mode == "local":
            return await self._run_local(question)
        return await self._run_platform(question)

    async def _run_local(self, question: str) -> dict:
        """本地直连：同进程直调，先联网检索原文，再把检索报告喂给条文核查官。"""
        self._emit("phase", phase="prepare", label="本地直连（测试辅助）· 仅用API密钥，无需平台身份；正式形态=平台互联模式")

        # 阶段一：检索官先行取原文；顾问/风险官并行；条文官拿检索报告作答
        self._emit("phase", phase="phase1", label="阶段一 · 三位专家并行独立作答（含联网检索）")
        if local_role_ready("researcher"):
            research_task = self._ask_local(
                "researcher",
                f"【原文检索】用户问题：{question}\n"
                "请检索与该问题直接相关的政策文件（国家部委+所在省市层面），给出条款级原文摘录+来源网址+发布日期+权威度分级。",
                # 检索官要"搜索→抓取→再搜索"多轮：沿用默认 16 步实测会 StepBudgetExhausted，
                # 靠重试才拿到结果（单角色耗时 9.5 分钟）。平台模式给的是 40 步，这里对齐。
                max_steps=40)
        else:
            research_task = asyncio.sleep(0, result="[未配置 SEARCH_API_KEY（博查），跳过联网检索]")
        others = asyncio.gather(
            self._ask_local("advisor", f"【阶段一·独立作答】用户问题：{question}\n请给出六要素执行清单，并给出整体置信度(0-1)。"),
            self._ask_local("challenger", f"【阶段一·风险初判】用户问题：{question}\n请按六维风险清单给出风险初判，并给出置信度(0-1)。"),
        )
        research_report, (advisor_out, challenger_out) = await asyncio.gather(research_task, others)
        statute_note = (f"\n\n【已为你准备好的检索报告（直接引用其中的条款与来源）】\n{research_report[:6000]}\n"
                        "如报告仍缺关键原文，如实标注【待核实】，不要尝试委派或编造文件名。") \
            if not str(research_report).startswith("[未配置") else "\n\n（联网检索未启用：请凭训练知识作答，务必标注来源档位，查无原文的项一律标【待核实】。）"
        statute_out = await self._ask_local(
            "statute",
            f"【阶段一·独立作答】用户问题：{question}\n请给出基于政策原文的结论，标注来源档位与置信度(0-1)，引用须条文级。{statute_note}")
        p1 = {"statute": statute_out, "advisor": advisor_out, "challenger": challenger_out}

        # 阶段二：交叉质询 + 用户代言人追问
        self._emit("phase", phase="phase2", label="阶段二 · 交叉质询与用户视角追问")
        answers_block = "\n\n".join(f"【{ROLE_NAMES_UI[r]}的结论】\n{p1[r]}" for r in EXPERTS)
        p2 = await self._ask_local(
            "challenger",
            f"【阶段二·交叉质询】用户问题：{question}\n\n{answers_block}\n\n"
            "请对以上结论逐条质询：无引用支撑/超时效/地域错配/数值可疑的结论重点打击，用质询单格式。", max_steps=14)
        p2b = await self._ask_local(
            "persona",
            f"【阶段二·用户代言人追问】用户问题：{question}\n\n风险审查官的质询记录：\n{p2[:3000]}\n\n"
            "请你以用户代言人的身份，站在办事企业/个人的立场，用大白话提出3个最关键的追问（每问注明为什么对用户重要）。", max_steps=10)

        # 阶段三：仲裁
        self._emit("phase", phase="phase3", label="阶段三 · 仲裁官汇总裁决")
        import re
        confs = {}
        for r in EXPERTS:
            m = re.search(r"置信[度分]?[:：]?\s*(0?\.\d+|1(?:\.0+)?)", p1[r])
            confs[r] = float(m.group(1)) if m else 0.5
        material = json.dumps({
            "专家独立结论": [
                {"expert": ROLE_NAMES_UI[r], "conclusion": p1[r][:2500],
                 "confidence": confs[r], "citation_ok": "《" in p1[r]} for r in EXPERTS
            ],
            "质询记录": p2[:4000],
            "用户代言人追问": p2b[:2000],
        }, ensure_ascii=False)
        p3 = await self._ask_local(
            "arbiter",
            f"【阶段三·仲裁】用户问题：{question}\n\n会诊材料：{material}\n\n"
            "请输出三段式仲裁意见（共识结论/存在分歧/待核实项 + 下一步行动清单），并回应代言人的追问。", max_steps=14)

        result = {"question": question, "phase1": p1, "phase2": p2, "phase2b": p2b,
                  "phase3": p3,
                  # 检索官的取证报告：此前只拼进条文核查官的提示词用掉、不落盘，
                  # 复盘/举证时看不到"到底抓了哪几份政策文件"，故原样存档。
                  "research": research_report,
                  "meta": {"finished_at": datetime.now().isoformat(timespec="seconds"),
                           "engine": "local"}}
        self._save(result)
        self._emit("phase", phase="done", label="会诊完成 · 已交付《会诊意见书》（本地直连模式）")
        return result

    def _save(self, result: dict) -> None:
        out_dir = Path("consultations")
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"webui_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    async def _run_platform(self, question: str) -> dict:
        self._emit("phase", phase="prepare", label="平台互联模式 · 装载主持身份 · 建立 AIP 会话")
        ensure_engine_workspace()
        import host as hostmod
        agent = hostmod.build_agent(with_acps=True)
        ep = os.environ.get("AGENT_TUNNEL", "host.127-0-0-1.nip.io:9101")
        runtime = WitRuntime(agent, workspace=str(ENGINE_WS),
                             acps=rtbase.make_acps_config(f"https://{ep}/acps/aip/v1"))

        # 阶段一：三位专家并行独立作答
        self._emit("phase", phase="phase1", label="阶段一 · 三位专家并行独立作答")
        p1_prompts = {
            "statute": f"【阶段一·独立作答】用户问题：{question}\n请给出基于政策原文的结论，标注来源档位与置信度(0-1)，引用须条文级。需要原文依据时请委派本队检索官核实并注明来源网址。",
            "advisor": f"【阶段一·独立作答】用户问题：{question}\n请给出六要素执行清单，并给出整体置信度(0-1)。",
            "challenger": f"【阶段一·风险初判】用户问题：{question}\n请按六维风险清单给出风险初判，并给出置信度(0-1)。",
        }
        results = await asyncio.gather(
            *[self._ask(runtime, r, p1_prompts[r]) for r in EXPERTS], return_exceptions=True)
        p1 = {r: (f"[委派异常 {type(res).__name__}: {str(res)[:120]}]" if isinstance(res, Exception) else res)
              for r, res in zip(EXPERTS, results)}

        # 阶段二：交叉质询（风险审查官主导）+ 用户代言人追问
        self._emit("phase", phase="phase2", label="阶段二 · 交叉质询与用户视角追问")
        answers_block = "\n\n".join(f"【{ROLE_NAMES_UI[r]}的结论】\n{p1[r]}" for r in EXPERTS)
        p2 = await self._ask(
            runtime, "challenger",
            f"【阶段二·交叉质询】用户问题：{question}\n\n{answers_block}\n\n"
            "请对以上结论逐条质询：无引用支撑/超时效/地域错配/数值可疑的结论重点打击，用质询单格式。")
        p2b = await self._ask(
            runtime, "persona",
            f"【阶段二·用户代言人追问】用户问题：{question}\n\n风险审查官的质询记录：\n{p2[:3000]}\n\n"
            "请你以用户代言人的身份，站在办事企业/个人的立场，用大白话提出3个最关键的追问（每问注明为什么对用户重要）。")

        # 阶段三：仲裁裁决
        self._emit("phase", phase="phase3", label="阶段三 · 仲裁官汇总裁决")
        import re
        confs = {}
        for r in EXPERTS:
            m = re.search(r"置信[度分]?[:：]?\s*(0?\.\d+|1(?:\.0+)?)", p1[r])
            confs[r] = float(m.group(1)) if m else 0.5
        material = json.dumps({
            "专家独立结论": [
                {"expert": ROLE_NAMES_UI[r], "conclusion": p1[r][:2500],
                 "confidence": confs[r], "citation_ok": "《" in p1[r]} for r in EXPERTS
            ],
            "质询记录": p2[:4000],
            "用户代言人追问": p2b[:2000],
        }, ensure_ascii=False)
        p3 = await self._ask(
            runtime, "arbiter",
            f"【阶段三·仲裁】用户问题：{question}\n\n会诊材料：{material}\n\n"
            "请输出三段式仲裁意见（共识结论/存在分歧/待核实项 + 下一步行动清单），并回应代言人的追问。")

        result = {"question": question, "phase1": p1, "phase2": p2, "phase2b": p2b,
                  "phase3": p3, "meta": {"finished_at": datetime.now().isoformat(timespec="seconds"),
                                         "engine": "platform"}}
        self._save(result)
        self._emit("phase", phase="done", label="会诊完成 · 已交付《会诊意见书》")
        return result


# ---------------- 会话状态与 HTTP 服务 ----------------

SESSIONS: dict[str, dict] = {}
SESSIONS_LOCK = threading.Lock()


def _any_running() -> bool:
    return any(s.get("running") for s in SESSIONS.values())


def run_consultation(sid: str, question: str) -> None:
    session = SESSIONS[sid]

    def on_event(ev: dict) -> None:
        session["events"].append(ev)
        if ev["kind"] == "phase":
            session["phase"] = ev["phase"]

    try:
        engine = ConsultEngine(on_event)
        session["result"] = asyncio.run(engine.run(question))
    except Exception as e:  # noqa: BLE001 —— 任何失败都要回到页面
        session["error"] = f"{type(e).__name__}: {e}"
        on_event({"kind": "phase", "phase": "error", "label": session["error"]})
    finally:
        session["running"] = False
        session["finished_at"] = datetime.now().isoformat(timespec="seconds")


def port_alive(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "PolicyConsultUI/1.0"

    def log_message(self, fmt, *args):  # 安静模式：访问日志只记异常
        pass

    # ---- helpers ----
    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, ctype: str) -> None:
        if not path.is_file():
            self._json({"error": "not found"}, 404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---- GET ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._file(STATIC_DIR / "index.html", "text/html")
        if path == "/api/health":
            mode = choose_mode()
            if mode == "platform":
                roles = {r: {"name": n, "up": port_alive(p)} for r, (n, p) in {
                    "host": ("会诊主持", 9101), "statute": ("条文核查官", 9102), "advisor": ("落地顾问", 9103),
                    "challenger": ("风险审查官", 9104), "arbiter": ("仲裁官", 9105),
                    "persona": ("用户代言人", 9106), "researcher": ("政策原文检索官", 9107)}.items()}
            else:
                roles = {r: {"name": n, "up": local_role_ready(r)} for r, n in {
                    "statute": "条文核查官", "advisor": "落地顾问", "challenger": "风险审查官",
                    "arbiter": "仲裁官", "persona": "用户代言人", "researcher": "政策原文检索官"}.items()}
            return self._json({"ok": all(v["up"] for v in roles.values()),
                               "mode": mode, "llm_ready": bool(rtbase.llm_ready("host")), "roles": roles})
        if path == "/api/history":
            items = []
            for f in sorted(Path("consultations").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
                try:
                    q = json.loads(f.read_text(encoding="utf-8")).get("question", "")[:60]
                except Exception:
                    q = "(读取失败)"
                items.append({"file": f.name, "question": q,
                              "time": datetime.fromtimestamp(f.stat().st_mtime).strftime("%m-%d %H:%M")})
            return self._json({"items": items})
        if path.startswith("/api/consult/"):
            sid = path.rsplit("/", 1)[-1]
            s = SESSIONS.get(sid)
            if not s:
                return self._json({"error": "no such session"}, 404)
            return self._json(s)
        if path.startswith("/api/consultation/"):
            name = path.rsplit("/", 1)[-1]
            if "/" in name or "\\" in name or ".." in name:
                return self._json({"error": "bad name"}, 400)
            f = Path("consultations") / name
            if not f.is_file():
                return self._json({"error": "not found"}, 404)
            return self._file(f, "application/json")
        return self._json({"error": "not found"}, 404)

    # ---- POST ----
    def do_POST(self):
        if self.path != "/api/consult":
            return self._json({"error": "not found"}, 404)
        try:
            length = int(self.headers.get("Content-Length", 0))
            question = json.loads(self.rfile.read(length) or b"{}").get("question", "").strip()
        except Exception:
            return self._json({"error": "bad request"}, 400)
        if not question:
            return self._json({"error": "question is empty"}, 400)
        with SESSIONS_LOCK:
            if _any_running():
                return self._json({"error": "已有一场会诊进行中，请等它结束"}, 409)
            sid = uuid.uuid4().hex[:12]
            SESSIONS[sid] = {"id": sid, "question": question, "events": [], "phase": "queued",
                             "running": True, "error": None, "result": None,
                             "started_at": datetime.now().isoformat(timespec="seconds")}
        threading.Thread(target=run_consultation, args=(sid, question), daemon=True).start()
        return self._json({"id": sid})


def main() -> None:
    STATIC_DIR.mkdir(exist_ok=True)
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[webui] 政策会诊控制台已启动: http://127.0.0.1:{PORT}  (Ctrl+C 退出)")
    print("[webui] 前提: serve 七角色在运行(./start_all.sh)；首次会诊会自动复制主持身份到独立工作区")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[webui] 已退出")


if __name__ == "__main__":
    main()
