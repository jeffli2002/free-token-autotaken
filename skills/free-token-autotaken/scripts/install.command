#!/bin/zsh
set -e
SCRIPT_DIR="${0:A:h}"
APP_DIR="${SCRIPT_DIR:h}"
PYTHON_BIN="$(command -v python3)"
if [[ -z "$PYTHON_BIN" ]] || ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "Python 3.10 or newer is required. Install Python, then retry." >&2
  exit 1
fi
/usr/bin/swiftc "$SCRIPT_DIR/claim.swift" -o "$SCRIPT_DIR/claim"
chmod 700 "$SCRIPT_DIR/claim"
mkdir -p "$HOME/Library/Application Support/FreeTokenAgent" "$HOME/Library/LaunchAgents"
cat > "$HOME/Library/LaunchAgents/org.free-token-autotaken.agent.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>org.free-token-autotaken.agent</string>
<key>ProgramArguments</key><array><string>$PYTHON_BIN</string><string>$SCRIPT_DIR/agent.py</string><string>run-due</string></array>
<key>StartCalendarInterval</key><dict><key>Minute</key><integer>5</integer></dict>
<key>StandardOutPath</key><string>$HOME/Library/Application Support/FreeTokenAgent/agent.log</string>
<key>StandardErrorPath</key><string>$HOME/Library/Application Support/FreeTokenAgent/agent-error.log</string>
</dict></plist>
EOF
/bin/launchctl bootout "gui/$(id -u)/org.free-token-autotaken.agent" 2>/dev/null || true
/bin/launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/org.free-token-autotaken.agent.plist"
echo '已安装后台计划：每小时第 5 分钟检查一次。电脑忙或客户端未运行时自动延后。'
