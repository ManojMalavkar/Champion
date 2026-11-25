#!/bin/sh
# Champion example program (M2, class delete): remove the result files of an LS-DYNA run
# (d3plot*, d3dump*, binout*), keep the input decks and the messag/d3hsp text output.
# Usage: clean_run_dir.sh DIR
set -eu
[ $# -eq 1 ] || { echo "usage: clean_run_dir.sh DIR" >&2; exit 2; }
folder="$1"
[ -d "$folder" ] || { echo "not a folder: $folder" >&2; exit 2; }
count=0
for file in "$folder"/d3plot* "$folder"/d3dump* "$folder"/binout*; do
  [ -f "$file" ] || continue
  rm -f -- "$file"
  count=$((count + 1))
done
echo "removed $count result file(s) from $folder"
