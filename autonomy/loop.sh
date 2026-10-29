#!/usr/bin/env bash
# 容器内的会话驱动：反复启动全新的 headless Claude Code 会话，直到所有者停止运行。
set -uo pipefail

MODEL="${AUTONOMY_MODEL:-opus}"
REPO="${AUTONOMY_REPO:?AUTONOMY_REPO is required}"
# 单个会话的挂起保护；到时只结束该会话，运行继续
SESSION_HOURS="${AUTONOMY_SESSION_HOURS:-8}"
# 连续这么多个会话没有改变任何 Git ref 后，每个会话前先冷却
IDLE_BEFORE_COOLDOWN="${AUTONOMY_IDLE_BEFORE_COOLDOWN:-3}"
IDLE_COOLDOWN_MINUTES="${AUTONOMY_IDLE_COOLDOWN_MINUTES:-60}"
STATE_DIR=/home/node/.autonomy
STOP_FILE="$STATE_DIR/STOP"

SESSION_SECONDS=$(awk "BEGIN{printf \"%d\", $SESSION_HOURS * 3600}")
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
LOG_DIR="$STATE_DIR/logs/$RUN_ID"
mkdir -p "$LOG_DIR"
rm -f "$STOP_FILE"

log() { echo "[autonomy $(date '+%F %T')] $*" | tee -a "$LOG_DIR/driver.log"; }

# 网络探测：模型代理和 GitHub 都能建立连接才算在线（任何 HTTP 响应都算通）
network_ok() {
  curl -sS -o /dev/null --max-time 15 "${ANTHROPIC_BASE_URL:-https://api.anthropic.com}" 2>/dev/null &&
    curl -sS -o /dev/null --max-time 15 https://api.github.com 2>/dev/null
}

# 可被 STOP 打断的等待；收到 STOP 时返回 1
pause() {
  local until=$(( $(date +%s) + $1 ))
  while [ "$(date +%s)" -lt "$until" ]; do
    [ -f "$STOP_FILE" ] && return 1
    sleep 10
  done
}

# 断网期间阻塞等待，不消耗会话和失败次数；收到 STOP 时返回 1
wait_for_network() {
  local delay=15
  network_ok && return 0
  log "network unreachable; waiting"
  while ! network_ok; do
    pause "$delay" || return 1
    [ "$delay" -lt 300 ] && delay=$(( delay * 2 ))
  done
  log "network restored"
}

if [ -z "${GH_TOKEN:-}" ]; then
  log "GH_TOKEN is not set; refusing to start."
  exit 1
fi
wait_for_network || { log "stopped before the network came up"; exit 0; }
gh auth setup-git >/dev/null
# 卡住的 HTTP 传输 60 秒内低于 1KB/s 即中止，避免 git 无限挂起
git config --global http.lowSpeedLimit 1000
git config --global http.lowSpeedTime 60
if ! login=$(gh api user --jq .login 2>/dev/null); then
  log "GH_TOKEN cannot access the GitHub API; refusing to start."
  exit 1
fi
# 公开仓库：默认用 GitHub noreply 邮箱提交，避免暴露真实邮箱
if [ -z "$(git config --global user.email)" ]; then
  git config --global user.name "${login}"
  git config --global user.email "$(gh api user --jq .id)+${login}@users.noreply.github.com"
fi

if [ ! -d /workspace/.git ]; then
  log "cloning $REPO into /workspace"
  for attempt in 1 2 3 4 5; do
    git clone --quiet "https://github.com/$REPO.git" /workspace && break
    rm -rf /workspace/.git
    [ "$attempt" -eq 5 ] && { log "clone failed"; exit 1; }
    sleep $(( attempt * 30 ))
  done
fi
cd /workspace

refs_fingerprint() {
  timeout 120 git fetch --quiet --prune origin 2>/dev/null
  git for-each-ref --format='%(refname) %(objectname)' | sha256sum
}

# 把 stream-json 转成可读的进度输出，原始日志另存
pretty() {
  jq -R -r --unbuffered '
    fromjson? |
    if .type == "assistant" then
      (.message.content[]? |
        if .type == "text" then "💬 " + .text
        elif .type == "tool_use" then "🔧 " + .name + " " + ((.input.description // .input.command // .input.file_path // "") | tostring | .[0:160])
        else empty end)
    elif .type == "result" then
      "== session result: " + (.subtype // "?") + ", turns=" + ((.num_turns // 0) | tostring)
    else empty end'
}

PROMPT_TEMPLATE=$(cat /opt/autonomy/prompt.md)
log "run $RUN_ID started; runs until stopped; model $MODEL"

session=0 idle=0 fails=0
while :; do
  [ -f "$STOP_FILE" ] && break
  if [ "$idle" -ge "$IDLE_BEFORE_COOLDOWN" ]; then
    log "$idle consecutive sessions without new commits; cooling down ${IDLE_COOLDOWN_MINUTES} min"
    pause $(( IDLE_COOLDOWN_MINUTES * 60 )) || break
  fi
  wait_for_network || break

  session=$(( session + 1 ))
  prompt="${PROMPT_TEMPLATE//\{\{SESSION\}\}/$session}"
  prompt="${prompt//\{\{IDLE\}\}/$idle}"

  before=$(refs_fingerprint)
  log "session $session starting"
  timeout --signal=TERM --kill-after=60 "$SESSION_SECONDS" \
    claude -p "$prompt" \
      --dangerously-skip-permissions \
      --model "$MODEL" \
      --output-format stream-json --verbose \
    < /dev/null 2>"$LOG_DIR/session-$session.stderr" \
    | tee "$LOG_DIR/session-$session.jsonl" | pretty
  code=${PIPESTATUS[0]}
  after=$(refs_fingerprint)
  log "session $session exited with code $code"

  if [ "$code" -eq 124 ] || [ "$code" -eq 137 ]; then
    log "session $session hit the ${SESSION_HOURS}h session limit; starting a fresh one"
  elif [ "$code" -ne 0 ]; then
    if ! network_ok; then
      log "session $session failed while offline; not counted as a failure"
      continue
    fi
    fails=$(( fails + 1 ))
    backoff=$(( 60 * (1 << (fails > 5 ? 5 : fails - 1)) )); [ "$backoff" -gt 900 ] && backoff=900
    log "backing off ${backoff}s after $fails consecutive failure(s)"
    pause "$backoff" || break
    continue
  fi
  fails=0

  if [ "$before" = "$after" ]; then
    idle=$(( idle + 1 ))
    log "no new commits in session $session ($idle consecutive)"
  else
    idle=0
  fi
done

log "run $RUN_ID stopped by the owner after $session session(s)"
