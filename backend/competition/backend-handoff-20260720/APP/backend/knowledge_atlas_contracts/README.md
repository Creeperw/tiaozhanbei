# Knowledge Atlas assets

`2026-07-18/manifest.json` is the tracked integrity contract for the immutable
teammate handoff. The approximately 2 GB runtime payload is intentionally kept
outside Git under `backend/knowledge_atlas_assets/` and
`backend/knowledge_atlas_runtime/`.

Import from the repository root:

```powershell
python -m APP.backend.scripts.import_knowledge_atlas_assets
```

The importer validates every source file before copying, rejects an existing
target that differs from the contract, copies into a sibling staging directory,
validates the copy, and only then promotes it into place. Re-running the command
with the same version and hashes is a no-op. Use `--verify-only` for a read-only
audit. `KNOWLEDGE_ATLAS_DATA_ROOT` names the final `backend_delivery` directory;
`KNOWLEDGE_ATLAS_VIDEO_ROOT` names the parent containing the three video
subdirectories. Both can override the default destinations.
