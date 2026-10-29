#!/bin/sh
# Install Resource Steward in an Arena workspace. No cloud credentials or daemon start.
set -eu
SRC=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEST=${RESOURCE_STEWARD_HOME:-${HOME:-/home/user}}
case "$DEST" in /*) ;; *) echo 'RESOURCE_STEWARD_HOME must be absolute' >&2; exit 2;; esac
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != '--enable-agent-guide' ]; }; then
  echo 'Usage: sh install.sh [--enable-agent-guide]' >&2
  exit 2
fi
if [ ! -f "$SRC/resource-steward/steward.py" ]; then
  echo 'Archive is incomplete: missing resource-steward/steward.py' >&2
  exit 2
fi
mkdir -p "$DEST/resource-steward/tests" "$DEST/bin"
for name in core.py state.py steward.py README.md; do
  cp "$SRC/resource-steward/$name" "$DEST/resource-steward/$name"
done
for name in test_cli.py test_core.py test_state.py; do
  cp "$SRC/resource-steward/tests/$name" "$DEST/resource-steward/tests/$name"
done
cat > "$DEST/bin/resource-steward" <<'LAUNCHER'
#!/bin/sh
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec python3 "$BASE/resource-steward/steward.py" "$@"
LAUNCHER
chmod 755 "$DEST/bin/resource-steward" "$DEST/resource-steward/steward.py"
if [ "${1:-}" = '--enable-agent-guide' ]; then
  MARKER='<!-- Resource Steward local workflow -->'
  if ! grep -Fq "$MARKER" "$DEST/AGENTS.md" 2>/dev/null; then
    printf '\n' >> "$DEST/AGENTS.md"
    cat "$SRC/AGENTS.snippet.md" >> "$DEST/AGENTS.md"
  fi
fi
printf 'Installed: %s/bin/resource-steward\n' "$DEST"
printf 'Check: %s/bin/resource-steward status\n' "$DEST"
printf 'Tests: python3 -m unittest discover -s %s/resource-steward/tests -v\n' "$DEST"
printf 'Monitor: start %s/bin/resource-steward daemon --interval 15 with a background-process tool\n' "$DEST"
