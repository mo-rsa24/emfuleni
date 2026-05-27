#!/usr/bin/env bash
# .claude/hooks/post_edit_check.sh
#
# Fires after Claude edits a file. Runs three deterministic checks and
# prints output for Claude to read. No judgment, no decisions.
#
# Reads the edited file path from stdin (JSON, from Claude Code) and
# only acts on Python files. Non-Python edits exit silently.
#
# Each check is gated on the tool being installed, so this works during
# setup (before ruff or Django are installed) without failing loudly.

set -u  # error on unset vars; do NOT use -e (we want to run all checks)

# --- Read the edited file path from the hook event JSON on stdin ---
# Claude Code sends a JSON event; we pull out the file path. If jq is not
# installed, fall back to a grep — works for the common case.
event_json="$(cat)"
if command -v jq >/dev/null 2>&1; then
  edited_file="$(echo "$event_json" | jq -r '.tool_input.file_path // .tool_input.path // empty')"
else
  edited_file="$(echo "$event_json" | grep -oE '"(file_path|path)"[[:space:]]*:[[:space:]]*"[^"]+"' | head -1 | sed 's/.*"\([^"]*\)"$/\1/')"
fi

# Only act on Python files. Quietly skip everything else.
case "$edited_file" in
  *.py) ;;
  *) exit 0 ;;
esac

# Only act if the file still exists (Claude may have deleted it).
[ -f "$edited_file" ] || exit 0

echo "[post-edit] checks on: $edited_file"
fail=0

# --- 1. Format (ruff format) ---
if command -v ruff >/dev/null 2>&1; then
  if ! ruff format --check "$edited_file" >/dev/null 2>&1; then
    echo "[post-edit] ruff format: would reformat — applying"
    ruff format "$edited_file" || fail=1
  fi
else
  echo "[post-edit] ruff not installed — skipping format"
fi

# --- 2. Lint (ruff check) ---
if command -v ruff >/dev/null 2>&1; then
  ruff_out="$(ruff check "$edited_file" 2>&1)" || {
    echo "[post-edit] ruff check found issues:"
    echo "$ruff_out"
    fail=1
  }
else
  echo "[post-edit] ruff not installed — skipping lint"
fi

# --- 3. Django check (project-wide, fast) ---
# Only runs if manage.py exists at the repo root.
if [ -f "manage.py" ]; then
  django_out="$(python manage.py check 2>&1)" || {
    echo "[post-edit] django check failed:"
    echo "$django_out"
    fail=1
  }
else
  echo "[post-edit] manage.py not found yet — skipping django check"
fi

if [ "$fail" -eq 0 ]; then
  echo "[post-edit] ok"
fi

# Exit 0 even on check failures — the output is what matters; Claude reads it
# and decides. A non-zero exit would block Claude's edit, which is too strict
# for an MVP. Tighten later if you want hard enforcement.
exit 0