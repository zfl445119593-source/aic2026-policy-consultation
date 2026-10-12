"""AMP 转发器：workspaces/*/logs/amp_*.jsonl → 平台Kafka(wt.ioa.pub:19092)

补齐 WIT 缺失的日志上报链路（官方架构=本地NDJSON→转发器→Kafka→Monitor→Discovery alive）。
断点续传：offsets.json 记录每文件已发送行数。

用法:
    ../.venv/Scripts/python.exe amp_forwarder.py            # 常驻
"""
import json
import time
from pathlib import Path

from kafka import KafkaProducer

BROKER = "wt.ioa.pub:19092"
LOG_GLOB = "amp_*_*.jsonl"          # amp_<type>_<aic>.jsonl
STATE_FILE = Path("fluent-bit-state/offsets.json")
POLL_SECONDS = 5

TYPE_MAP = {"heartbeat", "system", "access", "message", "metrics", "audit"}


def topic_for(filename: str) -> str | None:
    parts = filename.split("_")
    if len(parts) >= 2 and parts[1] in TYPE_MAP:
        return f"amp.{parts[1]}"
    return None


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def make_producer():
    return KafkaProducer(
        bootstrap_servers=BROKER,
        value_serializer=lambda v: v.encode("utf-8"),
        acks=1,
        request_timeout_ms=20000,
        retries=3,
    )


def main() -> None:
    producer = None
    while producer is None:
        try:
            producer = make_producer()
        except Exception as e:
            print(f"[amp-forwarder] Kafka未就绪({type(e).__name__})，10秒后重试...")
            time.sleep(10)
    print(f"[amp-forwarder] 已连接 {BROKER}，开始尾追 {LOG_GLOB}")
    state = load_state()
    while True:
        try:
            sent_any = False
            for f in sorted(Path("workspaces").glob(f"*/logs/{LOG_GLOB}")):
                topic = topic_for(f.name)
                if not topic:
                    continue
                key = str(f)
                pos = state.get(key, 0)
                try:
                    lines = f.read_text(encoding="utf-8").splitlines()
                except OSError:
                    continue
                if len(lines) < pos:
                    # 文件被重建/截断（如serve重启清日志）——偏移归零重发；
                    # 重复记录由Monitor按log_id去重，不丢数据优先
                    pos = 0
                for line in lines[pos:]:
                    line = line.strip()
                    if line:
                        producer.send(topic, value=line)
                        sent_any = True
                if len(lines) > pos:
                    state[key] = len(lines)
            if sent_any:
                try:
                    producer.flush(timeout=30)
                except Exception:
                    print("[amp-forwarder] 连接断开，重建Kafka连接...")
                    try: producer.close(timeout=5)
                    except Exception: pass
                    producer = None
                    while producer is None:
                        try: producer = make_producer()
                        except Exception:
                            print("[amp-forwarder] 重连中...")
                            time.sleep(10)
                    continue
                save_state(state)
                print(f"[amp-forwarder] {time.strftime('%H:%M:%S')} 已转发并保存进度（{len(state)}个文件）")
            time.sleep(POLL_SECONDS)
        except KeyboardInterrupt:
            print("[amp-forwarder] 停止")
            break
        except Exception as e:
            # 任何异常（含状态文件被外部删除等）只记录不退出——转发器中断=全队智能体平台离线
            print(f"[amp-forwarder] 周期异常({type(e).__name__}: {str(e)[:80]})，5秒后继续")
            time.sleep(5)
            state = load_state()
    try:
        producer.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
