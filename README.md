# Free Token Autotaken

A local macOS assistant that scans installed AI apps, matches them with free quota offers, and can help claim selected offers through an app's visible interface.

This repository also contains an installable Agent Skill at `skills/free-token-autotaken/`. The Skill performs official-source research and can use the user's selected workspace context to produce three groups: installed and usable, recommended to install with a short context-based reason, and other not installed. It does not install software or upload local files.

Install the Skill in Codex with the built-in GitHub skill installer using this repository and path `skills/free-token-autotaken`.

After installation, invoke it with `$free-token-autotaken` or ask Codex to find current free AI credits.

## Data sources

Activity data must come directly from model and agent vendors: official campaign pages, product documentation, pricing pages, developer docs, and official announcements. **AIHOT is a methodology reference only. This project does not import activity data from AIHOT.** New offers belong in `market_catalog.json` or `market_extra.json` only after a person verifies the vendor's official page, eligibility, quota type, scope, expiry, and claim route. `market_ingest.py` rejects non-HTTPS and AIHOT URLs and requires `verification_state` to be `厂商官方已核实`.

The repository starts with an empty offer catalog because campaigns change and must be checked against vendor sources before publication. Add current, verified offers for your region before expecting matches.

## Requirements

- macOS 13 or later
- Python 3.10+
- Swift compiler (included with Xcode Command Line Tools) for the optional Accessibility helper

## Run

1. Download or clone this repository.
2. Run `./start.command` to open the local dashboard.
3. To enable hourly background checks, run `./install.command` and grant Accessibility permission to the app or terminal that runs the helper.

The dashboard binds to `127.0.0.1` only. Installed-app scanning and claim history stay on the Mac. The scanner reads app bundle IDs and the Kimi Code CLI path; it does not read passwords, cookies, API keys, or browser profiles.

## Background claims and privacy

Background claims are opt-in through `install.command`. The helper only operates supported offers, only when the target app is already open, and only after the Mac has been idle for two minutes. It does not launch or activate apps. Accessibility automation can still change the target app's visible content while idle. Login challenges, changed layouts, or missing success feedback stop the attempt. Never enable a claim adapter unless you have reviewed the vendor's rules and the local UI flow.

Claim history is stored under `~/Library/Application Support/FreeTokenAgent/`. Nothing is uploaded by this project. Do not commit that directory, screenshots, app logs, browser profiles, account identifiers, cookies, API keys, or machine-specific binaries.

To uninstall the hourly job:

```sh
launchctl bootout "gui/$(id -u)/org.free-token-autotaken.agent"
rm "$HOME/Library/LaunchAgents/org.free-token-autotaken.agent.plist"
```

## Add verified offers

Prepare a JSON array using the existing catalog fields, set `verification_state` to `厂商官方已核实`, and use a direct vendor-owned HTTPS page in `official_url`. Then import it:

```sh
python3 market_ingest.py --input verified-offers.json --target market_extra.json
```

Do not add offers based only on aggregators, social reposts, or search snippets. Mark a source pending when official evidence is missing.

## Security

Please report security issues privately to the repository owner. Do not post credentials or personal data in issues.

## License

MIT. See [LICENSE](LICENSE).
