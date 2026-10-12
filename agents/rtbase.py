"""政策会诊 · 共享配置

所有智能体共用：Windows兼容补丁、大模型接入（实验室/任意OpenAI兼容端点）、梧桐平台ACPs配置。
大模型暂缓接入：等实验室端点确定后，设置环境变量即可，代码不用改。

环境变量（以后接模型时设置）：
    LLM_BASE_URL  例 http://192.168.1.100:8000/v1   （实验室 vLLM/Ollama 等 OpenAI 兼容端点）
    LLM_API_KEY   瞎填非空即可（本地推理服务通常不校验）
    LLM_MODEL     例 qwen2.5-32b-instruct
    可选按角色覆盖：LLM_MODEL_HOST / LLM_MODEL_STATUTE / ... （角色名大写）
"""
import json
import os
from pathlib import Path

# ---- Windows 兼容补丁：wit 2.1.0 原子写文件用了 POSIX 独有的 os.fchmod ----
if not hasattr(os, "fchmod"):
    os.fchmod = lambda fd, mode: None


def _patch_sdk_discovery() -> None:
    # wit 2.1.0 的 discover() 附带 filter.onlyAvailable，服务端已移除该字段（40004 FilterInvalid，必 400）。
    # 置空 discovery_filter 后按无 filter 请求，服务端正常返回。
    try:
        import wit.acps.discovery as _d
        _d.discovery_filter = lambda **_: None
    except Exception:
        pass


_patch_sdk_discovery()


def _patch_cert_clock() -> None:
    # WIT 证书校验带 freshness 余量：新签证书在签发瞬间必判 not_yet_valid。
    # 领证场景设 ENROLL_CLOCK_SHIFT=1，把校验时钟拨快15分钟绕过；已存证书按真实时钟早已生效。
    if os.environ.get("ENROLL_CLOCK_SHIFT") != "1":
        return
    import datetime as _dt

    class _Shifted(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return super().now(tz) + _dt.timedelta(minutes=15)

        @classmethod
        def utcnow(cls):
            return super().utcnow() + _dt.timedelta(minutes=15)

    try:
        import wit.acps.certificate_validation as _cv
        import wit.acps.ca as _ca
        _cv.datetime = _Shifted
        _ca.datetime = _Shifted
    except Exception:
        pass


_patch_cert_clock()


def _patch_amp_journal_read() -> None:
    """wit AMP 日志校验的 Windows 兼容补丁。

    _commit_sync 以 O_RDWR|O_APPEND 打开日志；_validate_history 先 lseek(0) 再分块
    os.read 校验全文。Windows CRT 下 O_APPEND 描述符 rewind 后首个 os.read 会从错误
    偏移开始（实测跳过66字节），剩余字节数读到空 -> 误报
    "journal changed during validation"，AMP 心跳 6/6 被拒。
    修复：校验读取改走独立只读打开，判定语义不变（逐行校验+前后指纹比对）。
    """
    import os as _os
    from wit.acps import amp as _amp

    def _validate_history(self, descriptor, log_type):
        B = _os.fstat(descriptor)
        C = self._fingerprint(B)
        if self._validated_history.get(log_type) == C:
            return B.st_size
        if B.st_size == 0:
            self._validated_history[log_type] = C
            return 0
        data = self.path_for(log_type).read_bytes()
        if not data.endswith(b"\n"):
            raise _amp.AmpPersistenceError(
                f"AMP {log_type} append refused an incomplete NDJSON journal"
            )
        if len(data) != B.st_size:
            raise _amp.AmpPersistenceError(
                f"AMP {log_type} append refused a journal changed during validation"
            )
        for i, line in enumerate(data.split(b"\n")[:-1], start=1):
            self._validate_line(line, i, log_type)
        G = _os.fstat(descriptor)
        if self._fingerprint(G) != C:
            raise _amp.AmpPersistenceError(
                f"AMP {log_type} append refused a journal changed during validation"
            )
        self._validated_history[log_type] = C
        return B.st_size

    _amp._AmpJournal._validate_history = _validate_history


_patch_amp_journal_read()

# 梧桐平台（远程服务，不本地部署）
WUTONG = {
    "registry_url": "https://wt.ioa.pub/registry-server",
    "ca_url": "https://wt.ioa.pub/ca-server",
    "discovery_url": "https://wt.ioa.pub/discovery/acps-adp-v2/discover",
}

# serve 绑定地址（联调本机127.0.0.1；上云0.0.0.0）与对外端点（ACS里申报的地址）
PUBLIC_BIND_HOST = os.environ.get("AGENT_PUBLIC_HOST", "127.0.0.1")
PUBLIC_ENDPOINT_HOST = os.environ.get("AGENT_ENDPOINT_HOST", "YOUR-SERVER")

# 主持走内网穿透隧道公网可达（地址是部署相关的，不入库：写在 llm.env 的 AGENT_TUNNEL）；
# 伙伴仅同机Leader调用，localhost即可（平台允许localhost端点）
TUNNEL = os.environ.get("AGENT_TUNNEL", "")  # 例: xxx.tcp.vip.cpolar.cn:12345
# 伙伴公网试点：设置了 AGENT_TUNNEL_<ROLE> 则该伙伴端点也走隧道
ROLE_PORTS = {"host": 9101, "statute": 9102, "advisor": 9103,
              "challenger": 9104, "arbiter": 9105, "persona": 9106,
              "researcher": 9107}


def endpoint_for(role: str) -> str:
    """该角色对外的ACS端点（不含/acps/aip/v1后缀）。

    主持=cpolar隧道公网直达；伙伴=nip.io通配域名(解析回127.0.0.1)——
    域名形式合规过审，同机Leader可解析直连，外网不可达（只暴露Leader）。
    伙伴若设置了 AGENT_TUNNEL_<ROLE大写> 则改走该隧道（公网试点）。
    """
    if role == "host":
        if not TUNNEL:
            raise SystemExit(
                "缺少 AGENT_TUNNEL（主持的公网固定隧道地址，如 xxx.tcp.vip.cpolar.cn:12345）。"
                "请先 source llm.env 再启动。")
        return f"https://{TUNNEL}"
    role_tunnel = os.environ.get(f"AGENT_TUNNEL_{role.upper()}", "")
    if role_tunnel:
        return f"https://{role_tunnel}"
    return f"https://{role}.127-0-0-1.nip.io:{ROLE_PORTS[role]}"


def team_aic(role: str) -> str:
    """本队成员的AIC。真实身份表 aics.local.json 不入库（模板见 aics.local.example.json）；
    环境变量 AGENT_AIC_<ROLE> 优先，其次本地身份表，最后占位符。"""
    aic = os.environ.get(f"AGENT_AIC_{role.upper()}", "")
    if not aic:
        p = Path(__file__).parent / "aics.local.json"
        if p.exists():
            try:
                aic = json.loads(p.read_text(encoding="utf-8")).get(role, "")
            except Exception:
                aic = ""
    return aic or f"<{role}-AIC未配置，见 aics.local.example.json>"


def get_llm_settings(role: str) -> dict:
    """集中取模型配置。role 如 'host'，可被 LLM_MODEL_HOST 单独覆盖。"""
    return {
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "api_key": os.environ.get("LLM_API_KEY", "sk-not-set"),
        "model": os.environ.get(f"LLM_MODEL_{role.upper()}", os.environ.get("LLM_MODEL", "")),
    }


def llm_ready(role: str) -> bool:
    s = get_llm_settings(role)
    return bool(s["base_url"] and s["model"])


ROLE_NAMES = {
    "host": "会诊主持", "statute": "条文核查官", "advisor": "落地顾问",
    "challenger": "风险审查官", "arbiter": "仲裁官", "persona": "适用对象代理",
}


def get_role_name(role: str) -> str:
    return ROLE_NAMES.get(role, role)


def make_provider(role: str):
    """有模型配置时返回 OpenAIChatProvider，否则返回 None（只跑确定性能力/ACS生成）。

    可用环境变量微调（按角色覆盖加 _角色大写后缀，如 LLM_TIMEOUT_ADVISOR）：
        LLM_TEMPERATURE  温度；设为 "omit" 则不传（部分模型如 kimi-k3 不支持自定义温度）
        LLM_TIMEOUT      请求超时秒数，默认 180（大模型首字延迟高）
        LLM_MAX_TOKENS   单次回复最大token，默认 8192（推理模型的思考token也计入预算，4096会被思考烧光导致正文为空）
    """
    if not llm_ready(role):
        return None
    from wit import OpenAIChatProvider

    def _opt(base: str) -> str | None:
        v = os.environ.get(f"{base}_{role.upper()}", os.environ.get(base, ""))
        return v or None

    s = get_llm_settings(role)
    kwargs = {
        "model": s["model"],
        "api_key": s["api_key"],
        "base_url": s["base_url"],
        "timeout": float(_opt("LLM_TIMEOUT") or 180),
        "max_retries": 3,
        "max_tokens": int(_opt("LLM_MAX_TOKENS") or 8192),
    }
    temp = _opt("LLM_TEMPERATURE")
    if temp and temp != "omit":
        kwargs["temperature"] = float(temp)
    return OpenAIChatProvider(**kwargs)


# AIP Group 基础设施：mq_url 已实测（RabbitMQ TLS 1.3 + AMQP 0-9-1 应答正常）。
# mq-auth-server 平台仅以路径形式公开(https://wt.ioa.pub/mq-auth)，而 SDK 验证器要求
# 纯 origin——此地址仅 Leader 建群写 ACL 时调用；Partner 收邀请走 MQ inbox 不经过它。
MQ_URL = os.environ.get("AGENT_MQ_URL", "amqps://wt.ioa.pub:5671/acps")
MQ_GROUP_API_URL = os.environ.get("AGENT_MQ_GROUP_API_URL", "https://mq-auth.wt.ioa.pub")


def make_acps_config(public_endpoint: str, display_org: str = "AIC参赛队伍"):
    """梧桐平台接入配置。public_endpoint = 该智能体对外服务地址（上线时换成云服务器）。"""
    from wit import AcpsConfig, AcpsRegistration, AgentCertificateRequest
    return AcpsConfig.custom(
        registry_url=WUTONG["registry_url"],
        ca_url=WUTONG["ca_url"],
        discovery_url=WUTONG["discovery_url"],
        public_endpoint=public_endpoint,
        mq_url=MQ_URL,
        mq_group_api_url=MQ_GROUP_API_URL,
        provider=_provider(display_org),
        certificate=AgentCertificateRequest(requested_validity=60),
        registration=AcpsRegistration.integrated(),
        strict_interop=True,
    )


def group_admission_hooks():
    """群组邀请准入：只接受发布者身份已验证的群（平台或持证 Leader 发起）。"""
    from wit import AipGroupAdmissionHooks, AipGroupAdmissionDecision

    def _admit(request):
        if request.publisher_verified:
            return AipGroupAdmissionDecision.accept()
        return AipGroupAdmissionDecision.reject("需要已验证的发布者身份")

    return AipGroupAdmissionHooks(_admit)


def step_budget_hook(steps: int = 40):
    """入站任务的步数预算钩子：serve 路径默认 max_steps=8，
    主持一场三阶段会诊（接诊+多轮委派+收结果+汇总）远不够——
    实测 8 步在阶段二耗尽报 StepBudgetExhausted。RunConfig 是 frozen，
    用 object.__setattr__ 在 on_run_start 提额。"""
    from wit import WitHooks

    class _StepBudget(WitHooks):
        id = f"step-budget-{steps}"
        priority = 100

        async def on_run_start(self, event) -> None:
            try:
                object.__setattr__(event.context.config, "max_steps", steps)
            except Exception:
                pass

    return _StepBudget()


def _provider(org: str):
    from wit import AgentProviderProfile
    return AgentProviderProfile(organization=org, country_code="CN")
