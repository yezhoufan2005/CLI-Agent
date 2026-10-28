set -e
[ "$(tr -d '[:space:]' < answer.txt)" = "4415" ]
# The small context budget must actually have forced compaction.
grep -q '"event": "compact"' .debug.jsonl
