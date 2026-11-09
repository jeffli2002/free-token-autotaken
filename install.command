#!/bin/zsh
set -e
cd "${0:A:h}"
/usr/bin/swiftc claim.swift -o claim
chmod 700 claim
mkdir -p "$HOME/Library/Application Support/FreeTokenAgent" "$HOME/Library/LaunchAgents"
cat > "$HOME/Library/LaunchAgents/com.jeff.free-token-agent.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>Label</key><string>org.free-token-autotaken.agent</string>
<key>ProgramArguments</key><array><string>/usr/local/bin/python3</string><string>${0:A:h}/agent.py</string><string>run-due</string></array>
<key>StartCalendarInterval</key><dict><key>Minute</key><integer>5</integer></dict>
<key>StandardOutPath</key><string>$HOME/Library/Application Support/FreeTokenAgent/agent.log</string>
<key>StandardErrorPath</key><string>$HOME/Library/Application Support/FreeTokenAgent/agent-error.log</string>
</dict></plist>
EOF
/bin/launchctl bootout "gui/$(id -u)/org.free-token-autotaken.agent" 2>/dev/null || true
/bin/launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.jeff.free-token-agent.plist"
echo '已安装后台计划：每小时第 5 分钟检查一次。电脑忙或客户端未运行时自动延后。'
