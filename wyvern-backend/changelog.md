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
- Added a cache-busting embed version to the landing page's deployed iframe fallback so Netlify-style mirrors pick up the latest app UI changes reliably.

## 2026-04-04

- Replaced icons
- Added an automatic changelog popup to the main chat app.
- Kept the release notes in markdown so updates stay easy to edit.
- The changelog now opens when users visit the site and can be reopened from the modal.
- Updated the landing page with a fallback launch link and configurable maintenance messaging.
