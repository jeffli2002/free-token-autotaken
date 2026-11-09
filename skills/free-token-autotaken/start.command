#!/bin/zsh
cd "${0:A:h}"
if /usr/bin/curl --silent --fail "http://127.0.0.1:8765/" >/dev/null; then
  open "http://127.0.0.1:8765"
  exit 0
fi
APP_DIR="${0:A:h}"
DATA_DIR="$HOME/Library/Application Support/FreeTokenAgent"
mkdir -p "$DATA_DIR"
/usr/bin/nohup /usr/bin/python3 "$APP_DIR/scripts/agent.py" serve >"$DATA_DIR/web.log" 2>&1 &
for i in {1..20}; do
  if /usr/bin/curl --silent --fail "http://127.0.0.1:8765/" >/dev/null; then
    open "http://127.0.0.1:8765"
    exit 0
  fi
  sleep 0.25
done
echo "Local dashboard failed to start; see $DATA_DIR/web.log" >&2
exit 1
