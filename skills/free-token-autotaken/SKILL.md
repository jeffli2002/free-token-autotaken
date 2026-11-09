---
name: free-token-autotaken
description: Find current free AI model and agent quotas from vendor official sources, match offers to installed tools and the user's selected work context, and recommend relevant installs. Use when searching, comparing, or claiming free AI credits or tokens.
---

# Free Token Autotaken

Use current vendor-controlled campaign pages, official product and developer docs, pricing pages, and announcements as evidence for offers. AIHOT may inform source discovery and collection methodology, but never import its activity listings as offer data. Search broadly across vendors before matching the local machine.

This Skill package includes the local web dashboard and macOS connector. When the user asks to open the dashboard, launch the bundled `start.command`; do not ask them to download a separate app. The optional background claim service is enabled only when the user explicitly chooses it, by running bundled `scripts/install.command` and granting macOS Accessibility permission.

## Workflow

1. Discover current offers across AI model API, coding agent, desktop agent, and related developer products. Confirm the offer on a vendor-controlled page. Record the source URL, checked date, eligibility, region, quota type, applicable products/models, expiry, and claim route. Treat aggregator-only mentions as unverified leads and exclude them from confirmed recommendations.
2. Match confirmed offers against apps the user says are installed or asks you to scan. Keep product credits, subscription quota, API credit, and free usage limits distinct.
3. When the user asks for personalized recommendations, inspect only the current project/workspace or a directory the user explicitly selects. Infer work context from project names, README/project descriptions, and dependency/build manifests. Do not recursively scan the home directory or unrelated folders. Skip credentials, .env*, browser data, account data, private keys, databases, and personal documents unless the user names a specific file for review. Summarize only the minimum context needed; do not copy source text or sensitive details into a report.
4. Classify each confirmed offer into exactly one group:
   - **Installed and usable:** relevant offer for a detected or user-confirmed installed app.
   - **Recommended to install:** relevant offer for an absent app, with a short reason tied to observed project context and an official download link. Do not install automatically.
   - **Other not installed:** remaining absent-app offers that do not fit the inferred work context.
   Offers that need no installation are shown separately and may still be recommended when relevant.
5. Explain why an offer fits, cite the official source, and label uncertainty. Ask before any account creation, software installation, or claim action that spends money or changes account settings. Never report a claim as successful without vendor confirmation.

## Privacy

Keep local app inventory, inspected paths, inferred work context, and claim history on the device. Do not upload files or context summaries to third-party services as part of the scan. If remote research is used for vendor offers, send only vendor/product search terms, never local file contents, paths, project names, or inferred business details.
