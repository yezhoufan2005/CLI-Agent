#!/usr/bin/env bash
# Print the CHANGELOG.md section for a version (without its heading).
set -euo pipefail
version="$1"
cd "$(dirname "$0")/.."
awk -v v="$version" '
  /^## / { if (found) exit; if ($2 == v || $2 == "["v"]") { found = 1; next } }
  found { print }
  END { if (!found) { print "no CHANGELOG section for " v > "/dev/stderr"; exit 1 } }
' CHANGELOG.md
