#!/bin/bash
# 一键拉起全栈：serve(7智能体) + amp_forwarder(日志→Kafka) + amp_message_emitter(消息适配)
# 用法（Git Bash，任意目录）: bash /e/CTF/wit/agents/start_all.sh
# 场景：重启电脑后 / 崩溃恢复后。铁律：日志和offsets文件一律不删（转发器自动断点续传）
cd /e/CTF/wit/agents || exit 1
source llm.env

PY=../.venv/Scripts/python.exe

alive() {  # 按命令行关键词查存活
    powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | Where-Object {\$_.CommandLine -like '*$1*'} | Measure-Object | Select-Object -ExpandProperty Count" 2>/dev/null
}

if [ "$(alive 'serve.py')" = "0" ]; then
    nohup $PY -u serve.py host statute advisor challenger arbiter persona researcher >> serve_run.log 2>&1 &
    echo "[start_all] serve 七角色已拉起"
else
    echo "[start_all] serve 已在运行，跳过"
fi

if [ "$(alive 'amp_forwarder')" = "0" ]; then
    nohup $PY -u amp_forwarder.py >> forwarder.log 2>&1 &
    echo "[start_all] 日志转发器已拉起"
else
    echo "[start_all] 转发器已在运行，跳过"
fi

if [ "$(alive 'amp_message_emitter')" = "0" ]; then
    nohup $PY -u amp_message_emitter.py >> emitter.log 2>&1 &
    echo "[start_all] 消息适配器已拉起"
else
    echo "[start_all] 适配器已在运行，跳过"
fi

echo "[start_all] 完成。验证：30秒后 tail -2 forwarder.log 应见'已转发'；netstat 应见9101-9107监听"
