#!/bin/sh
# 容器入口：加载本机配置 -> 基本校验 -> 交给进程守护
#
# 配置文件不在镜像里（见 .dockerignore），由 docker-compose 以只读卷挂到 /app/agents/。
# llm.env / .acps.env 用的是 `export VAR="..."` 语法，POSIX sh 的 `.` 可以直接加载，
# 所以容器无需再维护一份 .env。

set -eu

CONFIG_DIR="${AIC_CONFIG_DIR:-/app/agents}"

# Windows 编辑器写的配置通常是 CRLF；直接 source 会把行尾 \r 带进变量值，
# 空行更会被当成命令执行（实测报 "/app/agents/llm.env: : not found"）。
# 统一去掉 \r 后再加载。
load_env() {
    _src="$1"
    _tmp="$(mktemp)"
    tr -d '\r' < "$_src" > "$_tmp"
    . "$_tmp"
    rm -f "$_tmp"
}

if [ -f "$CONFIG_DIR/llm.env" ]; then
    load_env "$CONFIG_DIR/llm.env"
else
    echo "[entrypoint] 警告：未找到 $CONFIG_DIR/llm.env（大模型密钥缺失，WebUI 会无法作答）" >&2
fi

if [ -f "$CONFIG_DIR/.acps.env" ]; then
    load_env "$CONFIG_DIR/.acps.env"
fi

# 容器内必须监听 0.0.0.0，否则 docker 的端口映射进不来；
# 伙伴端口(9102-9107)不会被发布，仍在容器内网。
export AGENT_PUBLIC_HOST="${AGENT_PUBLIC_HOST:-0.0.0.0}"
export PYTHONUNBUFFERED=1
export PYTHONIOENCODING=utf-8

if [ -z "${AGENT_TUNNEL:-}" ]; then
    cat >&2 <<'EOF'
[entrypoint] 错误：AGENT_TUNNEL 未配置。
  serve.py 启动主持角色时需要它作为 ACS 对外端点（形如 8.tcp.cpolar.cn:14002）。
  请先在 agents/llm.env 里设置：
      export AGENT_TUNNEL="<cpolar 的 TCP 地址:端口>"
  然后重新 `docker compose up -d`。
EOF
    exit 1
fi

mkdir -p "${AIC_LOG_DIR:-/app/logs}" "$CONFIG_DIR/workspaces" "$CONFIG_DIR/consultations"
exec python /app/docker/supervise.py