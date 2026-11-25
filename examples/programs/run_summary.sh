#!/bin/sh
# Champion example program (M2, class read): summary of an LS-DYNA run folder.
# Usage: run_summary.sh --folder DIR [-v]
set -u
folder=""
verbose=0
while [ $# -gt 0 ]; do
  case "$1" in
    --folder) folder="$2"; shift 2 ;;
    -v) verbose=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -d "$folder" ] || { echo "not a folder: $folder" >&2; exit 2; }
echo "folder: $folder"
echo "files: $(find "$folder" -maxdepth 1 -type f | wc -l)"
echo "input decks: $(find "$folder" -maxdepth 1 -type f \( -name '*.k' -o -name '*.key' -o -name '*.dyn' \) | wc -l)"
echo "d3plot files: $(find "$folder" -maxdepth 1 -type f -name 'd3plot*' | wc -l)"
if [ -f "$folder/messag" ]; then
  if grep -q "N o r m a l    t e r m i n a t i o n" "$folder/messag"; then echo "status: normal termination"
  elif grep -q "E r r o r   t e r m i n a t i o n" "$folder/messag"; then echo "status: error termination"
  else echo "status: running or stopped"; fi
fi
if [ "$verbose" = 1 ]; then
  ls -l "$folder"
fi
