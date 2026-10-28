#!/usr/bin/env bash
# End-to-end tasks against a real model. Needs ANTHROPIC_API_KEY or
# ANTHROPIC_AUTH_TOKEN (+ ANTHROPIC_BASE_URL for a proxy). Not run in CI.
# Usage: scripts/e2e.sh [model]     Prints one PASS/FAIL line per task.
set -uo pipefail
cd "$(dirname "$0")/.."
MODEL="${1:-${CLI_AGENT_MODEL:-claude-sonnet-4-6}}"
STATE=$(mktemp -d)
pass=0 fail=0
for task in tests/e2e/*/; do
  name=$(basename "$task")
  work=$(mktemp -d)
  cp -r "$task/repo/." "$work/"
  start=$(date +%s)
  XDG_STATE_HOME="$STATE" timeout 600 uv run --quiet cli-agent -C "$work" --mode auto \
    --model "$MODEL" --json "$(cat "$task/prompt.txt")" > "$work/.events.jsonl" 2>"$work/.stderr"
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
