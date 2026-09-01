#!/bin/sh
# zodchiy installer.
#
# Copies the skill into every agent harness found on this machine. It never
# overwrites a file it did not write: a global AGENTS.md or GEMINI.md belongs
# to you and probably holds your own rules. When one is already there, the
# installer prints what to append instead of doing it for you.
#
#   ./install.sh              install into every harness found
#   ./install.sh --dry-run    print what would happen, touch nothing
#   ./install.sh --force      replace a previous zodchiy install
set -eu

SRC=$(cd "$(dirname "$0")" && pwd)
NAME=zodchiy
DRY=0
FORCE=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --force) FORCE=1 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

say() { echo "$@"; }
run() { if [ "$DRY" -eq 1 ]; then say "  would: $*"; else "$@"; fi; }

install_skill() {
  home=$1
  dest="$home/skills/$NAME"
  [ -d "$home" ] || { say "skip $home — not present"; return 0; }
  if [ -e "$dest" ] && [ "$FORCE" -eq 0 ]; then
    say "skip $dest — already exists (pass --force to replace)"
    return 0
  fi
  say "skill -> $dest"
  run rm -rf "$dest"
  run mkdir -p "$home/skills"
  run cp -R "$SRC" "$dest"
  # Установленный скилл — не рабочая копия репозитория: ни истории, ни
  # установщика внутри быть не должно.
  if [ "$DRY" -eq 0 ]; then
    rm -f "$dest/install.sh"
    rm -rf "$dest/.git"
  fi
}

install_doctrine() {
  file=$1
  home=$2
  [ -d "$home" ] || { say "skip $home — not present"; return 0; }
  dest="$home/$file"
  if [ -e "$dest" ]; then
    say "skip $dest — exists; append $SRC/dist/$file yourself, it is a router, not a whole doctrine"
    return 0
  fi
  say "doctrine -> $dest"
  run cp "$SRC/dist/$file" "$dest"
}

install_command() {
  rel=$1
  dest=$2
  home=$(dirname "$(dirname "$dest")")
  [ -d "$home" ] || { say "skip $home — not present"; return 0; }
  say "command -> $dest"
  run mkdir -p "$(dirname "$dest")"
  run cp "$SRC/dist/$rel" "$dest"
}

[ "$DRY" -eq 1 ] && say "dry run — nothing will be written"

install_skill "$HOME/.claude"
# Grok is reported to read ~/.grok/skills; unverified, so it is installed only
# when the directory already exists.
install_skill "$HOME/.grok"

install_doctrine AGENTS.md "$HOME/.codex"
install_doctrine GEMINI.md "$HOME/.gemini"
install_doctrine QWEN.md "$HOME/.qwen"
install_doctrine IFLOW.md "$HOME/.iflow"

install_command gemini/commands/$NAME.toml "$HOME/.gemini/commands/$NAME.toml"
install_command iflow/commands/$NAME.toml "$HOME/.iflow/commands/$NAME.toml"

say ""
say "done. Verify with: python3 $SRC/$NAME.py --help"
say "No adapter has ever been run inside its target harness — see README."
