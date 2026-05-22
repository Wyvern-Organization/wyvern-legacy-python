# Wyvern Deploy

The public site is served from `wyvernhub.net`, and the app lives on `app.wyvernhub.net`.

## Layout

- `landing/` contains the landing and research pages.
- `wyvern-backend/` contains the app backend and app shell.
- `/` should serve the landing page.
- `/landing` should resolve to the same landing page.

## Landing Updates

If the app URL changes, update the landing CTA in `landing/index.html`.

Keep `landing/_redirects` in place so `/landing` continues to resolve to the landing page on the static host.

## Publish

1. Check the worktree with `git status --short`.
2. Stage the intended files.
3. Commit with a clear message.
4. Push to the GitHub remote.

Example:

```bash
git add DEPLOY.md landing/index.html landing/research.html landing/_redirects
git commit -m "Update Wyvern landing routing"
git push origin main
```
