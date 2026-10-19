"""容器内进程守护：serve(7 角色) / amp_forwarder / webui。

为什么三个进程在同一个容器：见 Dockerfile 头部注释。

行为：
  * 子进程输出加 [name] 前缀后同时写容器 stdout 与 /app/logs/<name>.log；
  * 异常退出按指数退避重启；连续 5 次快速失败则放弃，避免无限空转；
  * serve 是必需进程：它放弃时整个容器退出（交给 compose 的 restart 策略）；
  * 收到 SIGTERM/SIGINT 先让子进程优雅退出 10 秒，再强杀。
"""
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
LOG_DIR = Path(os.environ.get("AIC_LOG_DIR", "/app/logs"))
PY = sys.executable
MAX_RAPID_FAILURES = 5
RAPID_FAILURE_SECONDS = 60

SERVICES = [
    ("serve", [PY, "-u", "serve.py",
               "host", "statute", "advisor", "challenger",
               "arbiter", "persona", "researcher"], True),
    ("forwarder", [PY, "-u", "amp_forwarder.py"], False),
    ("webui", [PY, "-u", "webui.py"], False),
]

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print("[supervisor] " + msg, flush=True)


class Service:
    def __init__(self, name: str, argv: list[str], essential: bool, stop: threading.Event):
        self.name = name
        self.argv = argv
        self.essential = essential
        self.stop = stop
        self.proc: subprocess.Popen | None = None
        self.gave_up = False
        self.log_path = LOG_DIR / f"{name}.log"

    def _pump(self, stream, handle) -> None:
        prefix = f"[{self.name}] "
        for line in iter(stream.readline, ""):
            text = line.rstrip("\n")
            with _print_lock:
                print(prefix + text, flush=True)
                handle.write(text + "\n")
                handle.flush()
        stream.close()

    def run(self) -> None:
        failures = 0
        while not self.stop.is_set():
            started = time.time()
            try:
                self.proc = subprocess.Popen(
                    self.argv, cwd=str(AGENTS_DIR), env=os.environ.copy(),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
            except Exception as exc:  # 解释器都起不来时不再重试
                log(f"{self.name} 启动失败：{type(exc).__name__}: {exc}")
                self.gave_up = True
                break

            handle = open(self.log_path, "a", encoding="utf-8")
            log(f"{self.name} 已启动 pid={self.proc.pid}")
            pump = threading.Thread(target=self._pump, args=(self.proc.stdout, handle), daemon=True)
            pump.start()
            rc = self.proc.wait()
            pump.join(timeout=3)
            handle.close()
            self.proc = None

            if self.stop.is_set():
                log(f"{self.name} 已退出 rc={rc}")
                break

            uptime = time.time() - started
            if uptime >= RAPID_FAILURE_SECONDS:
                failures = 0
            failures += 1
            log(f"{self.name} 异常退出 rc={rc}（运行 {uptime:.0f}s，连续失败 {failures} 次）")

            if failures >= MAX_RAPID_FAILURES:
                self.gave_up = True
                log(f"{self.name} 连续失败 {failures} 次，放弃重启")
                if self.essential:
                    log("serve 是必需进程，容器将退出")
                    self.stop.set()
                break

            delay = min(30, 2 ** failures)
            log(f"{self.name} 将在 {delay}s 后重启")
            if self.stop.wait(delay):
                break

    def terminate(self, grace: float = 10.0) -> None:
        proc = self.proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
        except Exception:
            return
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            log(f"{self.name} 未在 {grace:.0f}s 内退出，强制结束")
            try:
                proc.kill()
            except Exception:
                pass


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    services = [Service(name, argv, essential, stop) for name, argv, essential in SERVICES]

    def on_signal(signum, _frame):
        log(f"收到信号 {signum}，开始优雅停止…")
        stop.set()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    log(f"agents 目录={AGENTS_DIR}  日志目录={LOG_DIR}")
    threads = [threading.Thread(target=s.run, name=s.name, daemon=True) for s in services]
    for t in threads:
        t.start()

    while not stop.is_set():
        stop.wait(1)
        if all(s.gave_up for s in services):
            break

    stop.set()
    for s in services:
        s.terminate()
    for t in threads:
        t.join(timeout=5)

    log("已退出")
    return 0 if all(not s.gave_up or not s.essential for s in services) else 1


if __name__ == "__main__":
    sys.exit(main())