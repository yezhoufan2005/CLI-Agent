#!/usr/bin/env bash
# 宿主机入口：构建镜像，并在隔离容器中启动/观察/停止自治运行。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE=cli-agent-autonomy
CONTAINER=cli-agent-autonomy
SECRETS_FILE="${AUTONOMY_SECRETS_FILE:-$HOME/.config/cli-agent-autonomy/secrets.env}"
HOURS="${AUTONOMY_HOURS:-12}"

die() { echo "error: $*" >&2; exit 1; }

repo_slug() {
  git -C "$ROOT" remote get-url origin | sed -E 's#(\.git)?$##; s#^.*github\.com[:/]##'
}

# 合并 ~/.claude/settings.json 的 env 与 secrets 文件，生成临时 env-file；不回显任何值
write_env_file() {
  python3 - "$SECRETS_FILE" "$1" <<'PY'
import json, os, sys
from urllib.parse import urlparse, urlunparse

secrets_path, out_path = sys.argv[1], sys.argv[2]
env = {}
settings = os.path.expanduser("~/.claude/settings.json")
if os.path.exists(settings):
    env.update({k: str(v) for k, v in json.load(open(settings)).get("env", {}).items()})
if os.path.exists(secrets_path):
    for line in open(secrets_path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")

base = env.get("ANTHROPIC_BASE_URL")
if base:
    u = urlparse(base)
    if u.hostname in ("localhost", "127.0.0.1", "::1"):
        netloc = "host.docker.internal" + (f":{u.port}" if u.port else "")
        env["ANTHROPIC_BASE_URL"] = urlunparse(u._replace(netloc=netloc))

missing = [] if os.environ.get("AUTONOMY_SKIP_GH_CHECK") else [k for k in ("GH_TOKEN",) if not env.get(k)]
if not (env.get("ANTHROPIC_AUTH_TOKEN") or env.get("ANTHROPIC_API_KEY")):
    missing.append("ANTHROPIC_AUTH_TOKEN or ANTHROPIC_API_KEY")
if missing:
    sys.exit("missing: " + ", ".join(missing) + f" (set them in {secrets_path})")
if any("\n" in v for v in env.values()):
    sys.exit("env values must be single-line")

fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    for k, v in env.items():
        f.write(f"{k}={v}\n")
print("env keys passed to container:", ", ".join(sorted(env)))
PY
}

with_env_file() {
  local env_file
  env_file="$(mktemp)"
  trap 'rm -f "$env_file"' RETURN
  write_env_file "$env_file"
  "$@" "$env_file"
}

build() {
  # 断网时拉取基础镜像元数据会失败；已有镜像则继续用旧镜像
  if ! docker build -t "$IMAGE" "$ROOT/autonomy"; then
    docker image inspect "$IMAGE" >/dev/null 2>&1 || die "image build failed"
    echo "warning: build failed, reusing existing $IMAGE image" >&2
  fi
}

running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" = "true" ]
}

docker_run_loop() {
  local env_file="$1" seconds
  seconds=$(awk "BEGIN{printf \"%d\", $HOURS * 3600}")
  docker rm "$CONTAINER" >/dev/null 2>&1 || true
  docker run -d --init --name "$CONTAINER" \
    -v cli-agent-workspace:/workspace \
    -v cli-agent-home:/home/node \
    --env-file "$env_file" \
    -e AUTONOMY_REPO="$(repo_slug)" \
    -e AUTONOMY_HOURS="$HOURS" \
    -e AUTONOMY_MODEL="${AUTONOMY_MODEL:-opus}" \
    "$IMAGE" \
    timeout --kill-after=120 "$seconds" /opt/autonomy/loop.sh >/dev/null
}

docker_smoke() {
  local env_file="$1"
  docker run --rm --init --env-file "$env_file" "$IMAGE" bash -c '
    set -u
    echo "claude: $(claude --version)"
    echo -n "model API: "; claude -p "Reply with exactly: OK" --model "${AUTONOMY_MODEL:-opus}" 2>&1 | tail -1
    echo -n "GitHub login: "; gh api user --jq .login 2>&1
    echo -n "repo permissions: "; gh api "repos/$0" --jq ".permissions" 2>&1
  ' "$(repo_slug)"
}

case "${1:-help}" in
  smoke)
    build
    AUTONOMY_SKIP_GH_CHECK=1 with_env_file docker_smoke
    ;;
  start)
    running && die "a run is already in progress (./autonomy/run.sh status)"
    build
    with_env_file docker_run_loop
    echo "started: $(repo_slug), up to ${HOURS}h. Follow with: ./autonomy/run.sh logs"
    ;;
  logs)
    docker logs -f "$CONTAINER"
    ;;
  status)
    docker ps -a --filter "name=^${CONTAINER}$" --format 'container: {{.Status}}'
    docker exec "$CONTAINER" bash -c 'tail -n 15 "$(ls -d ~/.autonomy/logs/*/ | tail -1)driver.log"' 2>/dev/null || true
    ;;
  stop)
    if [ "${2:-}" = "--now" ]; then
      docker stop -t 30 "$CONTAINER"
    else
      docker exec "$CONTAINER" touch /home/node/.autonomy/STOP
      echo "STOP requested: the current session finishes, no new session starts. Use 'stop --now' to kill it."
    fi
    ;;
  shell)
    if running; then
      docker exec -it "$CONTAINER" bash
    else
      # 运行已结束：用同一组卷开一个不带凭证的临时容器查看现场
      docker run --rm -it -v cli-agent-workspace:/workspace -v cli-agent-home:/home/node "$IMAGE" bash
    fi
    ;;
  fetch-logs)
    mkdir -p "$ROOT/autonomy/logs"
    docker cp "$CONTAINER:/home/node/.autonomy/logs/." "$ROOT/autonomy/logs/"
    echo "copied to autonomy/logs/"
    ;;
  *)
    echo "usage: $0 {smoke|start|logs|status|stop [--now]|shell|fetch-logs}"
    ;;
esac
