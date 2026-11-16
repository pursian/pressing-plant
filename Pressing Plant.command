#!/bin/bash
# Double-click launcher for pressingPlant.py. Runs the copy that sits next to this file
# ("LOSSLESS HD/Pressing Plant"), so nothing needs to be copied anywhere.
set -u
here="$(cd "$(dirname "$0")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1

echo "[Pressing Plant] user: $(whoami)"
echo "[Pressing Plant] script: $here/pressingPlant.py"
# Input: the Soulseek "complete" folder if this account has one, else ~/Downloads/MyFolder.
# Output goes OUTSIDE the Soulseek folder so converted files are never re-shared.
slsk="$HOME/Soulseek Downloads/complete"
if [ -d "$slsk" ]; then
  echo "[Pressing Plant] input:  $slsk"
  echo "[Pressing Plant] output: $HOME/Downloads/Lossless"
  python3 -u "$here/pressingPlant.py" --input "$slsk" --output "$HOME/Downloads/Lossless"
else
  python3 -u "$here/pressingPlant.py"
fi
exitCode=$?

echo ""
echo "[Pressing Plant] finished (python exit code: $exitCode)"
echo "Press Enter to close..."
read
