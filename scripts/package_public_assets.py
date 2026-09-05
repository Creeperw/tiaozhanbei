"""Package public assets only; never collect application runtime or databases."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tarfile


EXCLUDED = {
    "__pycache__", ".git", ".venv", "node_modules", ".pytest_cache",
    "uploads", "user_questions", "workshop_note_images", "snapshots",
    "debug_traces", "runtime", "logs", ".cache",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def files(root: Path):
    # Resolve only the explicitly selected root; nested links cannot escape it.
    root = root.resolve(strict=True)
    for directory, children, names in os.walk(root):
        children[:] = sorted(name for name in children if name not in EXCLUDED)
        for name in children:
            if (Path(directory) / name).is_symlink():
                raise ValueError(f"Nested directory symlink: {Path(directory) / name}")
        for name in sorted(names):
            path = Path(directory) / name
            if name.startswith(".env") or name.endswith((".pyc", ".log", ".db", ".sqlite", ".sqlite3", ".pem", ".key")):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Not a regular public asset: {path}")
            yield path, path.relative_to(root)


def package(output: Path, name: str, sources: list[tuple[Path, str]]) -> dict:
    target = output / f"{name}.tar.gz"
    manifest = output / f"{name}.files.jsonl"
    if target.exists() or manifest.exists():
        raise FileExistsError(f"Refusing to overwrite finished package: {name}")
    temporary = target.with_suffix(target.suffix + ".partial")
    manifest_temp = manifest.with_suffix(manifest.suffix + ".partial")
    count = total = 0
    with tarfile.open(temporary, "w:gz", compresslevel=1, format=tarfile.PAX_FORMAT) as archive, manifest_temp.open("w", encoding="utf-8") as records:
        for root, prefix in sources:
            for path, relative in files(root):
                before = path.stat()
                member = f"{prefix}/{relative.as_posix()}"
                digest = sha256(path)
                info = archive.gettarinfo(str(path), arcname=member)
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise RuntimeError(f"Asset changed while packaging: {path}")
                records.write(json.dumps({"path": member, "bytes": before.st_size, "sha256": digest}, ensure_ascii=False) + "\n")
                count += 1
                total += before.st_size
    if not count:
        raise ValueError(f"Empty asset package: {name}")
    # Fully decompress every entry to detect truncated or corrupt gzip streams.
    with tarfile.open(temporary, "r|gz") as archive:
        for entry in archive:
            stream = archive.extractfile(entry)
            if stream is not None:
                with stream:
                    while stream.read(8 * 1024 * 1024):
                        pass
    temporary.rename(target)
    manifest_temp.rename(manifest)
    result = {"file": target.name, "bytes": target.stat().st_size,
              "sha256": sha256(target), "file_count": count, "unpacked_bytes": total,
              "manifest": manifest.name, "manifest_sha256": sha256(manifest)}
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("component", "video", "vectors", "chapters", "textbooks", "treekg", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=True)
    indexes = list((args.vectors / "indexes").rglob("index.faiss"))
    if not indexes or any(not p.with_name("metadata.jsonl").is_file() for p in indexes):
        raise ValueError("Vectors require nonempty indexes with matching metadata.jsonl")
    for root in (args.component, args.video, args.vectors, args.chapters, args.textbooks, args.treekg):
        if not root.is_dir():
            raise ValueError(f"Missing asset directory: {root}")
        if args.output.resolve().is_relative_to(root.resolve()):
            raise ValueError("Output must not be inside a source directory")
    groups = [
        ("shizhen-20260905-knowledge", [(args.component, "assets/knowledge/releases/2026-07-18/component"), (args.video, "assets/knowledge/releases/2026-07-18/video")]),
        ("shizhen-20260905-vectors", [(args.vectors, "assets/vectors/public/2026-07-18")]),
        ("shizhen-20260905-graphs", [(args.chapters, "assets/knowledge-atlas/chapters/releases/2026-07-22"), (args.treekg, "app/TreeKG-main/src/data")]),
        ("shizhen-20260905-textbooks-13", [(args.textbooks / "十三五", "assets/textbooks/public/十三五")]),
        ("shizhen-20260905-textbooks-14", [(args.textbooks / "十四五", "assets/textbooks/public/十四五")]),
    ]
    results = [package(args.output, name, sources) for name, sources in groups]
    index = args.output / "PACKAGES.json"
    index.write_text(json.dumps({"release": "20260905", "scope": "public assets; no user database or runtime", "packages": results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checks = [(item["sha256"], item["file"]) for item in results]
    checks += [(item["manifest_sha256"], item["manifest"]) for item in results]
    checks.append((sha256(index), index.name))
    (args.output / "SHA256SUMS").write_text("".join(f"{digest}  {name}\n" for digest, name in checks), encoding="utf-8")


if __name__ == "__main__":
    main()