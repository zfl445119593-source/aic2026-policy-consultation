# syntax=docker/dockerfile:1
#
# AIC2026 政策会诊 · 一键部署镜像
#
# 为什么 7 个角色 + AMP 转发器 + WebUI 都在同一个容器里：
#   1) 七个角色之间用 https://<role>.127-0-0-1.nip.io:910x 互访，域名解析到 127.0.0.1，
#      拆成多个容器后 "127.0.0.1" 指向各自容器，伙伴端点会全部失联；
#   2) AMP 转发器读 workspaces/*/logs 下的账本，WebUI 也读写同一份 workspaces/consultations，
#      共享同一挂载卷最省事、也不会出现跨容器锁竞争。
# 因此只发布 9101（主持对外端点，cpolar 指向它）与 9300（WebUI）；
# 9102-9107 是队内伙伴端口，仅容器内可达。

# ---------- builder：只负责装依赖到 /install ----------
FROM python:3.13-slim AS builder

WORKDIR /build
# 平台框架 wheel（需自行从梧桐平台下载后放在仓库根目录）
COPY acps_sdk-2.2.0-py3-none-any.whl ./
COPY wit_framework-2.1.0-cp312.cp313.cp314-none-any.whl ./

# kafka-python 是 amp_forwarder 的依赖（仓库原有安装步骤未列出，这里补齐）
RUN pip install --no-cache-dir --no-compile --prefix=/install \
        ./acps_sdk-2.2.0-py3-none-any.whl \
        ./wit_framework-2.1.0-cp312.cp313.cp314-none-any.whl \
        "kafka-python==2.3.2"

# ---------- runtime ----------
FROM python:3.13-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    TZ=Asia/Shanghai \
    AIC_LOG_DIR=/app/logs

COPY --from=builder /install /usr/local

# 非 root 运行（uid 固定，便于宿主侧调整挂载目录属主）
RUN useradd --create-home --uid 10001 --shell /bin/sh aic \
 && mkdir -p /app/agents/workspaces /app/agents/consultations /app/logs \
 && chown -R aic:aic /app

WORKDIR /app
# 只拷代码；密钥/证书/历史记录由 compose 以卷注入（见 .dockerignore）
COPY --chown=aic:aic agents /app/agents
COPY --chown=aic:aic docker /app/docker

USER aic

# 9101 = 主持对外端点；9300 = WebUI。9102-9107 仅容器内可达，故不发布。
EXPOSE 9101 9300

HEALTHCHECK --interval=30s --timeout=6s --start-period=45s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9300/api/health', timeout=4).status == 200 else 1)"

ENTRYPOINT ["/bin/sh", "/app/docker/entrypoint.sh"]