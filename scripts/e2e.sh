#!/usr/bin/env bash
# End-to-end tasks against a real model. Needs ANTHROPIC_API_KEY or
# ANTHROPIC_AUTH_TOKEN (+ ANTHROPIC_BASE_URL for a proxy). Not run in CI.
# Usage: scripts/e2e.sh [model]     Prints one PASS/FAIL line per task.
set -uo pipefail
cd "$(dirname "$0")/.."
MODEL="${1:-${CLI_AGENT_MODEL:-claude-sonnet-4-6}}"
STATE=$(mktemp -d)
pass=0 fail=0
FILTER="${E2E_FILTER:-}"
for task in tests/e2e/*/; do
  [ -n "$FILTER" ] && [[ "$(basename "$task")" != *$FILTER* ]] && continue
  name=$(basename "$task")
  work=$(mktemp -d)
  cp -r "$task/repo/." "$work/"
  start=$(date +%s)
  extra=()
  # Optional per-task flags, one shell word per whitespace-separated token.
  [ -f "$task/args.txt" ] && read -r -a extra < "$task/args.txt"
  XDG_STATE_HOME="$STATE" timeout 600 uv run --quiet cli-agent -C "$work" --mode auto \
    --model "$MODEL" "${extra[@]}" --json "$(cat "$task/prompt.txt")" \
    > "$work/.events.jsonl" 2>"$work/.stderr"
  code=$?
  if (cd "$work" && bash "$OLDPWD/$task/verify.sh") >"$work/.verify" 2>&1; then
    result=PASS; pass=$((pass + 1))
  else
    result=FAIL; fail=$((fail + 1))
  fi
  turns=$(tail -1 "$work/.events.jsonl" | jq -r '.turns // "?"' 2>/dev/null)
  echo "$result $name exit=$code turns=$turns secs=$(( $(date +%s) - start )) workdir=$work"
done
echo "e2e model=$MODEL pass=$pass fail=$fail"
[ "$fail" -eq 0 ]
