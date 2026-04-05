# Wyvern Changelog

## 2026-04-05

- Added message replies with inline reply previews in chat and a reply composer bar before send.
- Added connected message rendering so consecutive posts from the same person merge into a cleaner stacked layout.
- Added built-in `GIPHY` support in the message bar with a picker for built-in GIFs and direct GIF URLs.
- Added a changeable user status control in the user panel for `Online`, `Idle`, and `Do Not Disturb`.
- Added live speaking indicators inside voice chat so active speakers glow and animate while talking.
- Extended message payloads to carry reply metadata and reaction summaries so chat updates stay consistent in history loads and real-time events.
- Fixed the landing-page mirror launcher so it can open mirrored root and nested app paths, and mirror redirects now stay on `/mirror/...`.
- Added a `/landing/mirror/...` shell that fetches mirrored app HTML and injects it into the current page so root loads stay on the landing mirror URL without visible redirects.
- Added a localhost-safe landing mirror fallback so `/landing/mirror/` can load the local app shell from `/` when the external mirror proxy is not configured.
- Made the landing mirror portable for deployed hosts and Netlify by allowing the landing page to launch mirror mode from its own URL with a `?mirror=` target and fall back to a full-page embedded app view when proxy injection is unavailable.
- Added a backend startup schema check that stops the app with a clear `alembic upgrade head` message when the database revision is behind the code.
- Updated the landing maintenance banner to use a shorter `Notice` label and support a separate custom `message` field in addition to estimated time.
- Refined chat replies so they render as connected message references above the author line, staying visually distinct from emoji reactions.
- Fixed reaction updates so emoji reaction chips appear immediately from both direct clicks and live socket events.
- Locked `edge_release_banner` to the Edge channel so Stable promotions cannot surface the banner to Stable users.
- Added a cache-busting embed version to the landing page's deployed iframe fallback so Netlify-style mirrors pick up the latest app UI changes reliably.
- Added a browser-local `Edge Mode` in `Settings -> For Devs` with a required `I understand` warning gate before users can enter the live development environment.
- Wired the Edge Mode switch itself to the same enable/disable handler as the button so the toggle animates and responds to clicks instead of acting like a dead indicator.
- Made Edge Mode disable cleanly exit embedded edge shells, clear edge-session tokens, and route direct `/edge` tabs back to Stable instead of leaving the browser stuck in Edge.
- Routed Edge websocket traffic through `/edge/ws` alongside `/edge/api/v1` so the edge client uses the same path namespace for both realtime and HTTP traffic.
- Made chat sends update the message list immediately after a successful post, and loosened message event channel matching to avoid missing live Edge updates.
- Replaced the old profile-only entry point with a lightweight settings hub that now includes `Account` and `For Devs`, while keeping profile editing inside the app.
- Added runtime Edge configuration, stable-to-edge iframe session handoff, and a stable-hosted Edge shell with a persistent banner, `Return to Stable`, and `Reload Edge`.
- Added separate-environment sync metadata, replication outbox/inbound ledger tables, signed internal sync endpoints, and a background bridge worker so compatible writes can flow between `main` and `edge` without sharing a database.
- Seeded the Edge environment from `main` through a signed bootstrap sync path and made `main` authoritative for conflict handling so rejected or incompatible Edge writes do not overwrite stable data.
- Moved Edge entry to a same-host `/edge` endpoint behind an `EDGE_MODE_ENABLED` feature flag so users no longer need separate stable/edge public URLs.
- Removed the public stable/edge app URL runtime wiring and now derive the Edge shell target from the current origin plus `/edge`.
- Added a DB-backed release-flag system with `/admin` promotion controls so Edge-only feature flags can be promoted into Stable with an audit trail instead of requiring a deploy swap.
- Added a release-channel runtime payload so the SPA can resolve Stable vs Edge feature flags from the backend and show Edge-only UI only inside the Edge channel.
- Tightened the sync bridge so only shared chat records and DM-related changes can cross from Edge back into Stable before promotion, while structural and experimental Edge writes stay out of Stable.

## 2026-04-04

- Replaced icons
- Added an automatic changelog popup to the main chat app.
- Kept the release notes in markdown so updates stay easy to edit.
- The changelog now opens when users visit the site and can be reopened from the modal.
- Updated the landing page with a fallback launch link and configurable maintenance messaging.
