"""按角色执行 WIT 注册状态机（up_until_ready）：对接已过审的注册记录并领取证书。

用法（Git Bash，在 agents 目录）：
    source llm.env && source .acps.env
    ENROLL_CLOCK_SHIFT=1 \
        ../.venv/Scripts/python.exe -u enroll_role.py statute [advisor ...]
    # 伙伴若需公网端点，另加 AGENT_TUNNEL_STATUTE=<隧道地址>

前提：对应角色的 ACS 版本已在平台过审（CLI agent save+submit）。
时钟拨快补丁在 rtbase 内由 ENROLL_CLOCK_SHIFT=1 激活（绕过新证书 freshness 校验）。
"""
import asyncio
import sys

import rtbase  # Windows补丁 + discovery补丁 + 时钟补丁
from wit import WitRuntime, RegistryPasswordCredentials


async def enroll(role: str) -> None:
    module = __import__(role)
    agent = module.build_agent(with_acps=True)
    runtime = WitRuntime(
        agent,
        workspace=f"workspaces/{role}",
        acps=rtbase.make_acps_config(rtbase.endpoint_for(role) + "/acps/aip/v1"),
    )
    print(f"[{role}] 开始注册状态机...", flush=True)
    result = await runtime.up_until_ready(credentials=RegistryPasswordCredentials.from_env())
    d = result.to_dict() if hasattr(result, "to_dict") else result
    print(f"[{role}] 结果: {d}", flush=True)


async def main() -> None:
    for role in sys.argv[1:]:
        try:
            await enroll(role)
        except Exception as e:
            print(f"[{role}] 失败: {type(e).__name__}: {str(e)[:300]}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
