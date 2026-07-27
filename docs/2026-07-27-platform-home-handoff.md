# 2026-07-27 Platform Home Handoff

## Scope

This change updates the platform home shell and top navigation while preserving
the existing application routes. The affected frontend entry points are:

- `frontend/llm/src/appShell.js`
- `frontend/llm/src/components/AppShell.jsx`
- `frontend/llm/src/components/HomePage.jsx`
- `frontend/llm/src/components/PlatformHome.css`
- `frontend/llm/src/index.css`

The matching Vitest coverage is updated in the adjacent `*.test.jsx` files.

## Video Asset

The home page video source is:

```text
/platform-assets/home/platform-agents.mp4
```

The deployable asset is committed at:

```text
backend/competition_app/static/platform-assets/home/platform-agents.mp4
```

`backend/competition_app/api/app.py` mounts this directory at
`/platform-assets`, and `frontend/llm/vite.config.js` proxies the same path to
the FastAPI service during local Vite development.

The supplied `test1.mp4` was moved from `other/` into this deployable static
asset path and transcoded from HEVC to H.264. Keep the video encoded as H.264
with the `avc1` MP4 marker: `test_platform_video_asset_is_public_and_uses_h264`
checks this explicitly and the format is needed for broad browser playback.

## Verification

Run the backend test from `backend/` with the package path set:

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m pytest competition_app/tests/api/test_auth.py -q
```

Run the focused frontend tests and production build from `frontend/llm/`:

```powershell
npm run test:unit -- src/appShell.test.jsx src/components/AppShell.test.jsx src/components/HomePage.test.jsx
npm run build
```

The checks passed when this handoff was written: backend `14 passed`, frontend
`35 passed`, and the Vite production build completed successfully.

## Local Files Not Included

The repository worktree also contains local-only directories and unrelated
drafts, including `.claude/`, `.vscode/`, `artifacts/`, `node_modules/`,
`other/`, and the two existing 2026-07-26 training-workshop plan drafts. They
are intentionally not part of this change.
