# Free Token Autotaken

A local macOS assistant that scans installed AI apps, matches them with free quota offers, and can help claim selected offers through an app's visible interface.

This repository ships as one installable Agent Skill package at `skills/free-token-autotaken/`. The same package contains the Skill, local web dashboard, and macOS connector. Users install once; they do not download a separate desktop app. It can use the user's selected workspace context to produce three groups: installed and usable, recommended to install with a short context-based reason, and other not installed. It does not install software automatically.

Install the Skill in Codex with the built-in GitHub skill installer using this repository and path `skills/free-token-autotaken`. After installation, run the bundled `start.command` to open its local web interface. The connector and UI assets are inside the same Skill folder.

After installation, invoke it with `$free-token-autotaken` or ask Codex to find current free AI credits.

## Data sources

Activity data must come directly from model and agent vendors: official campaign pages, product documentation, pricing pages, developer docs, and official announcements. **AIHOT is a methodology reference only. This project does not import activity data from AIHOT.** New offers belong in `skills/free-token-autotaken/market_catalog.json` or `market_extra.json` only after a person verifies the vendor's official page, eligibility, quota type, scope, expiry, and claim route. Every offer needs a stable `product_id` shared by activities for the same product and a `product_type` (`llm`, `coding_agent`, `agent`, `creative`, or `developer_platform`). `scripts/market_ingest.py` rejects non-HTTPS and AIHOT URLs and requires `verification_state` to be `厂商官方已核实`.

The repository starts with an empty offer catalog because campaigns change and must be checked against vendor sources before publication. Add current, verified offers for your region before expecting matches.

## Requirements

- macOS 13 or later
- Python 3.10+
- Swift compiler (included with Xcode Command Line Tools) for the optional Accessibility helper

## Run

1. Install the Skill from this repository.
2. Run the bundled `start.command` to open the local dashboard.
3. For a recurring offer, choose its “每日领取” or “每月领取” button to create that offer's local Skill and scheduled task. This is opt-in per offer. The task checks hourly and only attempts claims when the target app is already open and the Mac has been idle for two minutes.

The dashboard binds to `127.0.0.1` only. Installed-app scanning and claim history stay on the Mac. The scanner reads app bundle IDs and the Kimi Code CLI path; it does not read passwords, cookies, API keys, or browser profiles.

In **更多**, offers are grouped by product type. **× 不再显示** saves the product ID locally, hiding all current and later offers for that product. **恢复全部已隐藏产品** reverses that preference.

Installed activities are grouped by one-time, daily, and monthly cycles. When at least two one-time activities remain to process, the dashboard offers a batch claim flow with per-activity progress and official links for steps requiring user action. When at least two supported daily activities need a schedule, a separate batch action creates their daily tasks after checking for existing Skills and tasks across local Agents. Already-running external tasks do not count toward the batch threshold. A check mark appears only after the client confirms a claim or the scheduler confirms task creation.

## Background claims and privacy

Background claims are opt-in for each recurring offer through its “每日领取” or “每月领取” button. The helper only operates supported offers, only when the target app is already open, and only after the Mac has been idle for two minutes. It does not launch or activate apps. Accessibility automation can still change the target app's visible content while idle. Login challenges, changed layouts, or missing success feedback stop the attempt. Unsupported activities cannot be scheduled. Pause a task using its schedule button in the dashboard.

Before creating any claim Skill or schedule, the connector scans known local Skill directories for Codex, WorkBuddy, Cursor, Kimi Code, DeepSeek, and shared Agent Skills. It also checks active WorkBuddy automations through its local database in read-only mode, Codex automation metadata, the user's crontab, loaded macOS LaunchAgents, and this project's saved schedules. Each supported desktop offer needs activity-specific duplicate terms. A matching external task blocks creation and is labeled with its owner in the dashboard. A matching Skill without a confirmed task also blocks creation so it cannot silently create a second claim flow. Proprietary task stores with no readable local metadata cannot be verified; detection must not be described as exhaustive for those apps.

“暂停本助手代领” disables only this project's schedule entry for that offer. It leaves the Skill file, the shared macOS LaunchAgent, and any WorkBuddy or other app's internal Skill untouched. An external task detected by the dashboard has no pause control here.

Claim history is stored under `~/Library/Application Support/FreeTokenAgent/`. Nothing is uploaded by this project. Do not commit that directory, screenshots, app logs, browser profiles, account identifiers, cookies, API keys, or machine-specific binaries.

To uninstall the hourly job:

```sh
launchctl bootout "gui/$(id -u)/org.free-token-autotaken.agent"
rm "$HOME/Library/LaunchAgents/org.free-token-autotaken.agent.plist"
```

## Add verified offers

Prepare a JSON array using the existing catalog fields, set `verification_state` to `厂商官方已核实`, and use a direct vendor-owned HTTPS page in `official_url`. Then import it:

```sh
python3 skills/free-token-autotaken/scripts/market_ingest.py --input verified-offers.json --target market_extra.json
```

Do not add offers based only on aggregators, social reposts, or search snippets. Mark a source pending when official evidence is missing.

## Security

Please report security issues privately to the repository owner. Do not post credentials or personal data in issues.

## License

MIT. See [LICENSE](LICENSE).
