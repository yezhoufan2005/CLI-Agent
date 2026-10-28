# 自治 Agent 的隔离运行环境，由 autonomy/run.sh 在宿主机上构建。
FROM node:24.18.0-bookworm

ARG CLAUDE_CODE_VERSION=2.1.309

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      sudo git curl ca-certificates gnupg jq ripgrep less unzip zip procps \
      build-essential python3 python3-venv python3-pip \
 && mkdir -p -m 755 /etc/apt/keyrings \
 && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
      -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
 && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
      > /etc/apt/sources.list.d/github-cli.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends gh \
 && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11.31 /uv /uvx /usr/local/bin/

RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" \
 && npm cache clean --force

# 容器即边界：容器内给 agent 免密 sudo，便于按需安装工具链
RUN echo 'node ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/node \
 && chmod 440 /etc/sudoers.d/node \
 && mkdir -p /workspace \
 && chown node:node /workspace

# 驱动和提示词在构建时固化，运行中 agent 对仓库副本的修改不影响本次运行
COPY loop.sh prompt.md /opt/autonomy/
RUN chmod 755 /opt/autonomy/loop.sh

USER node
# 网络不稳定：放宽 Claude Code 的 API 重试次数和 npm 的下载重试
ENV PATH=/home/node/.local/bin:/home/node/.npm-global/bin:/home/node/.cargo/bin:/home/node/go/bin:$PATH \
    NPM_CONFIG_PREFIX=/home/node/.npm-global \
    DISABLE_AUTOUPDATER=1 \
    CLAUDE_CODE_MAX_RETRIES=20 \
    NPM_CONFIG_FETCH_RETRIES=5 \
    NPM_CONFIG_FETCH_RETRY_MAXTIMEOUT=120000
WORKDIR /workspace
CMD ["sleep", "infinity"]
