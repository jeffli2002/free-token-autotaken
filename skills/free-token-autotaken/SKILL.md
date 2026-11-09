---
name: free-token-autotaken
description: Find current free AI model and agent quotas from vendor official sources, match offers to installed tools and the user's selected work context, and recommend relevant installs. Use when searching, comparing, or claiming free AI credits or tokens.
---

# Free Token Autotaken

Use current vendor-controlled campaign pages, official product and developer docs, pricing pages, and announcements as evidence for offers. AIHOT may inform source discovery and collection methodology, but never import its activity listings as offer data. Search broadly across vendors before matching the local machine.

This Skill package includes the local web dashboard and macOS connector. When the user asks to open the dashboard, launch the bundled `start.command`; do not ask them to download a separate app. Offers are split by claim cycle: one-time claims use “立即领取”; recurring claims offer the current-period action and, when a verified client adapter exists, a separate opt-in schedule action. For WorkBuddy Buddy 加油站, show the daily task card on this device when a local successful client claim or an enabled schedule exists; the adapter only claims when the signed-in client displays “每日可领” and “立即领取”, and the actual points vary by campaign. Choosing a recurring schedule creates a per-offer local Skill and uses the macOS LaunchAgent. It only operates when the target app is already open and the Mac has been idle for two minutes; unsupported offers keep scheduling disabled. Users can pause each schedule in the dashboard.

Before creating a schedule for any offer, scan known local Skill directories for Codex, WorkBuddy, Cursor, Kimi Code, DeepSeek, and shared Agent Skills. Check active WorkBuddy automations through its local database in read-only mode, Codex automation metadata, crontab, loaded macOS LaunchAgents, and this package's schedule state. Match against the offer ID/name or its activity-specific `duplicate_terms`. Preserve an existing Skill and task; show the owner and detected status in the activity card. If a Skill is present but its task cannot be verified, stop creation until the duplicate is resolved. Do not claim exhaustive coverage of proprietary task stores without readable local metadata.

The dashboard's pause action affects only the schedule entry managed by this package. It does not pause or delete WorkBuddy's own Skills or tasks, nor any task belonging to another app.

## Workflow

1. Discover current offers across AI model API, coding agent, desktop agent, and related developer products. Confirm the offer on a vendor-controlled page. Record the source URL, checked date, eligibility, region, quota type, applicable products/models, expiry, and claim route. Treat aggregator-only mentions as unverified leads and exclude them from confirmed recommendations.
2. Match confirmed offers against apps the user says are installed or asks you to scan. Keep product credits, subscription quota, API credit, and free usage limits distinct.
3. When the user asks for personalized recommendations, inspect only the current project/workspace or a directory the user explicitly selects. Infer work context from project names, README/project descriptions, and dependency/build manifests. Do not recursively scan the home directory or unrelated folders. Skip credentials, .env*, browser data, account data, private keys, databases, and personal documents unless the user names a specific file for review. Summarize only the minimum context needed; do not copy source text or sensitive details into a report.
4. Show matched installed offers in the installed section. Show all other verified offers under **更多**, grouped by product type: LLM / 模型 API, Coding Agent, 通用 Agent, 创作工具, or 开发平台. Keep the `product_id` stable across activities for the same product. When a user selects **× 不再显示**, save that product ID locally and filter its current and future activities until the user explicitly restores hidden products. Do not install software automatically.
5. Explain why an offer fits, cite the official source, and label uncertainty. Ask before any account creation, software installation, or claim action that spends money or changes account settings. Never report a claim as successful without vendor confirmation.

## Privacy

Keep local app inventory, inspected paths, inferred work context, and claim history on the device. Do not upload files or context summaries to third-party services as part of the scan. If remote research is used for vendor offers, send only vendor/product search terms, never local file contents, paths, project names, or inferred business details.
