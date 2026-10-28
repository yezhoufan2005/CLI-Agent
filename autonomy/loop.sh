#!/usr/bin/env bash
# 容器内的会话驱动：在截止时间前反复启动全新的 headless Claude Code 会话。
set -uo pipefail

HOURS="${AUTONOMY_HOURS:-12}"
MODEL="${AUTONOMY_MODEL:-opus}"
REPO="${AUTONOMY_REPO:?AUTONOMY_REPO is required}"
MAX_IDLE="${AUTONOMY_MAX_IDLE_SESSIONS:-3}"
MAX_FAILS="${AUTONOMY_MAX_FAILED_SESSIONS:-5}"
MIN_SESSION_SECONDS="${AUTONOMY_MIN_SESSION_SECONDS:-600}"
STATE_DIR=/home/node/.autonomy
STOP_FILE="$STATE_DIR/STOP"

START=$(date +%s)
DEADLINE=$(( START + $(awk "BEGIN{printf \"%d\", $HOURS * 3600}") ))
RUN_ID=$(date -u +%Y%m%dT%H%M%SZ)
LOG_DIR="$STATE_DIR/logs/$RUN_ID"
mkdir -p "$LOG_DIR"
rm -f "$STOP_FILE"

log() { echo "[autonomy $(date '+%F %T')] $*" | tee -a "$LOG_DIR/driver.log"; }

if [ -z "${GH_TOKEN:-}" ]; then
  log "GH_TOKEN is not set; refusing to start."
  exit 1
fi
gh auth setup-git >/dev/null
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
  git clone --quiet "https://github.com/$REPO.git" /workspace || { log "clone failed"; exit 1; }
fi
cd /workspace

refs_fingerprint() {
  git fetch --quiet --prune origin 2>/dev/null
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
DEADLINE_TEXT="$(date -u -d "@$DEADLINE" '+%Y-%m-%d %H:%M UTC')"
log "run $RUN_ID started; deadline $DEADLINE_TEXT; model $MODEL"

session=0 idle=0 fails=0 reason="deadline reached"
while :; do
  now=$(date +%s)
  remaining=$(( DEADLINE - now ))
  if [ -f "$STOP_FILE" ]; then reason="STOP file present"; break; fi
  if [ "$remaining" -lt "$MIN_SESSION_SECONDS" ]; then break; fi

  session=$(( session + 1 ))
  prompt="${PROMPT_TEMPLATE//\{\{SESSION\}\}/$session}"
  prompt="${prompt//\{\{DEADLINE\}\}/$DEADLINE_TEXT}"
  prompt="${prompt//\{\{REMAINING_MIN\}\}/$(( remaining / 60 ))}"

  before=$(refs_fingerprint)
  log "session $session starting ($(( remaining / 60 )) min left)"
  timeout --signal=TERM --kill-after=60 "$remaining" \
    claude -p "$prompt" \
      --dangerously-skip-permissions \
      --model "$MODEL" \
      --output-format stream-json --verbose \
    < /dev/null 2>"$LOG_DIR/session-$session.stderr" \
    | tee "$LOG_DIR/session-$session.jsonl" | pretty
  code=${PIPESTATUS[0]}
  after=$(refs_fingerprint)
  log "session $session exited with code $code"

  if [ "$code" -eq 124 ]; then break; fi
  if [ "$code" -ne 0 ]; then
    fails=$(( fails + 1 ))
    if [ "$fails" -ge "$MAX_FAILS" ]; then reason="$fails consecutive failed sessions"; break; fi
    backoff=$(( 60 * (1 << (fails - 1)) )); [ "$backoff" -gt 900 ] && backoff=900
    log "backing off ${backoff}s after failure $fails/$MAX_FAILS"
    sleep "$backoff"
    continue
  fi
  fails=0

  if [ "$before" = "$after" ]; then
    idle=$(( idle + 1 ))
    log "no new commits in session $session ($idle/$MAX_IDLE idle)"
    if [ "$idle" -ge "$MAX_IDLE" ]; then reason="$idle consecutive sessions without new commits"; break; fi
  else
    idle=0
  fi
done

log "run $RUN_ID finished after $session session(s): $reason"
