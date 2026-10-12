"""serve 模式：让指定角色常驻监听（mTLS Direct + AMP心跳）。

按官方手册《AIP Direct·Partner》与《AMP》最佳实践：
  up_until_ready 领证 → serve_direct_partner 常驻（await Event().wait()）→ Ctrl+C 时 stop_serving()

用法（Git Bash）:
    cd /e/CTF/wit/agents
    source llm.env
    export ACPS_USERNAME=... ACPS_PASSWORD=...     # 首次注册需要
    ../.venv/Scripts/python.exe serve.py host [statute advisor ...]   # 默认host
环境变量:
    AGENT_PUBLIC_HOST 默认 127.0.0.1（联调；上云 0.0.0.0）
"""
import asyncio
import sys

import rtbase  # Windows补丁 + 模型配置
from wit import WitRuntime

ROLE_PORTS = {"host": 9101, "statute": 9102, "advisor": 9103,
              "challenger": 9104, "arbiter": 9105, "persona": 9106,
              "researcher": 9107}

RUNTIMES: dict[str, WitRuntime] = {}


async def serve_role(role: str, bind_host: str) -> None:
    module = __import__(role)
    port = ROLE_PORTS[role]
    agent = module.build_agent(with_acps=True)
    runtime = WitRuntime(
        agent,
        workspace=f"workspaces/{role}",
        acps=rtbase.make_acps_config(rtbase.endpoint_for(role) + "/acps/aip/v1"),
    )
    RUNTIMES[role] = runtime

    # 手册建议：部署前统一校验装配
    validation = await runtime.validate()
    ok = validation.get("valid") if isinstance(validation, dict) else getattr(validation, "valid", True)
    if not ok:
        print(f"[{role}] 装配校验失败: {validation}")
        return

    # 直连+群组同进程。注意：AMP 只随直连分支引导一次——
    # serve(direct=True, group=True) 会并发引导两次 AMP，日志校验竞争导致
    # "journal changed during validation" 心跳被拒（实测 6/6 复现），故顺序拆开。
    status = await runtime.serve_direct_partner(host=bind_host, port=port, amp=True)
    status2 = await runtime.serve_group(amp=False)
    for st in (status, status2):
        sd = st.to_dict() if hasattr(st, "to_dict") else st
        print(f"[{role}] {bind_host}:{port} -> {sd}")


async def main() -> None:
    roles = [a for a in sys.argv[1:] if not a.startswith("-")] or ["host"]
    bind_host = rtbase.PUBLIC_BIND_HOST
    await asyncio.gather(*[serve_role(r, bind_host) for r in roles])
    print("[serve] 全部角色已启动，常驻运行中（Ctrl+C 优雅停止）...")
    await asyncio.Event().wait()  # 官方要求的常驻方式


async def shutdown() -> None:
    for role, rt in RUNTIMES.items():
        try:
            await rt.stop_serving()
            print(f"[{role}] 已优雅停止")
        except Exception as e:
            print(f"[{role}] 停止异常: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[serve] 收到停止信号...")
        asyncio.run(shutdown())
