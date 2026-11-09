#!/bin/zsh
cd "${0:A:h}"
if /usr/bin/curl --silent --fail "http://127.0.0.1:8765/" >/dev/null; then
  open "http://127.0.0.1:8765"
  exit 0
fi
/usr/local/bin/python3 agent.py serve &
server_pid=$!
sleep 1
open "http://127.0.0.1:8765"
wait "$server_pid"
