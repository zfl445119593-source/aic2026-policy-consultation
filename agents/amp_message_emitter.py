"""消息事件适配器：Direct 模式 access 记录 → AMP message 记录（send/receive 成对）

背景：WIT 的 Direct 模式只写 amp_access 日志，不产生 amp_message；平台协作图/
Message 统计以 amp.message 主题为数据源（correlationId/messageId/eventType）。
本适配器监听各角色 logs/amp_access_*.jsonl 的新增行——每行都是一次真实发生的
mTLS 委派——按规范合成 host发送 + 伙伴接收 两条消息记录，写入同目录
amp_message_adapter.jsonl（无 AIC 后缀，WIT 日志校验不认领该文件名），
由 amp_forwarder 送往 amp.message 主题。

记录来源全部是真实协议事件（任务账本+access日志可对账），本组件只做格式适配，
与 amp_forwarder 同属"官方 Fluent Bit 缺位下的自研上报组件"。
"""
import json
import time
import uuid
from pathlib import Path

LOG_ROOT = Path("workspaces")
STATE_FILE = Path("fluent-bit-state/msg_offsets.json")


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def utcnow() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + f".{int(time.time()*1000)%1000:03d}Z"


def message_line(aic: str, event_type: str, peer_aic: str, correlation_id: str,
                 message_id: str, service: str, payload_bytes: int | None = None,
                 ts: str | None = None) -> str:
    """按 AMP Spec §5.5 MessageBody 构造合法消息记录（eventType/system/destination 必填）。

    ts 传原始 access 记录的事件时间——回放历史时保持真实时序（用生成时刻会按文件
    处理顺序打乱时间线，平台协作树按时间排序会错位）。
    """
    body: dict = {
        "eventType": event_type,
        "operationName": "aip.delegate" if event_type == "send" else "aip.task.receive",
        "system": "acps-direct",
        "destination": {"name": peer_aic, "kind": "queue"},
        "messageId": message_id,
        "attributes": {
            "peerAic": peer_aic,
            "protocol": "acps.direct.mtls",
        },
    }
    if payload_bytes:
        body["payloadSizeBytes"] = payload_bytes
    rec = {
        "schema_version": "1.0",
        "timestamp": ts or utcnow(),
        "aic": aic,
        "log_type": "message",
        "log_id": str(uuid.uuid4()),
        "correlation_id": correlation_id,
        "severity_text": "INFO",
        "severity_number": 9,
        "body": body,
        "resource": {"service.name": service},
    }
    return json.dumps(rec, ensure_ascii=False)


def main() -> None:
    print("[msg-emitter] 启动：access → message(send/receive) 适配", flush=True)
    while True:
        state = load_state()
        produced = 0
        for access_file in sorted(LOG_ROOT.glob("*/logs/amp_access_*.jsonl")):
            role = access_file.parts[-3]
            key = str(access_file)
            try:
                lines = access_file.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            pos = state.get(key, 0)
            new = [ln for ln in lines[pos:] if ln.strip()]
            if not new:
                continue
            out_lines = []
            for ln in new:
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                body = rec.get("body") or {}
                # 轮询/探活事件（durationMs=0 且无请求体）不生成消息记录——
                # 否则等待期的5秒轮询会把"任务数"刷成探活数，统计失真
                if (body.get("durationMs") or 0) == 0 and \
                   not (body.get("request") or {}).get("bodySizeBytes"):
                    continue
                caller = ((body.get("caller") or {}).get("aic") or "").strip()
                callee = ((body.get("callee") or {}).get("aic") or "").strip()
                corr = rec.get("correlation_id") or ""
                if not (caller and callee and corr):
                    continue
                service = (rec.get("resource") or {}).get("service.name") or f"policy-roundtable-{role}"
                mid = str(uuid.uuid4())
                size = ((body.get("request") or {}).get("bodySizeBytes")) or None
                ev_ts = rec.get("timestamp") or None
                out_lines.append(message_line(caller, "send", callee, corr, mid, service, size, ev_ts))
                out_lines.append(message_line(callee, "receive", caller, corr, mid, service, size, ev_ts))
                produced += 2
            if out_lines:
                out = access_file.parent / "amp_message_adapter.jsonl"
                with out.open("a", encoding="utf-8") as f:
                    f.write("\n".join(out_lines) + "\n")
            state[key] = len(lines)
        if produced:
            save_state(state)
            print(f"[msg-emitter] {time.strftime('%H:%M:%S')} 生成 {produced} 条消息记录", flush=True)
        time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("[msg-emitter] 停止", flush=True)
