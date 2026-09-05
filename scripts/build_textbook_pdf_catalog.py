from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


def clean_title(stem: str) -> str:
    title = re.sub(r"^\d+[-_\s]*", "", stem).strip()
    return re.sub(r"\s+", " ", title)


def aliases_for(title: str) -> list[str]:
    aliases = {title}
    if title.endswith("科学"):
        aliases.add(title[:-1])
    elif title.endswith("学") and len(title) > 2:
        aliases.add(title[:-1])
    reorder = {
        "内科学 中西医结合": "中西医结合内科学",
        "外科学 中西医": "中西医结合外科学",
    }
    if title in reorder:
        aliases.add(reorder[title])
    return sorted(aliases)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_catalog(pdf_root: Path) -> dict:
    books = []
    for path in sorted(pdf_root.rglob("*.pdf")):
        relative = path.relative_to(pdf_root)
        title = clean_title(path.stem)
        edition = relative.parts[0] if len(relative.parts) > 1 else "未分类"
        identity = f"{edition}/{title}".encode("utf-8")
        books.append({
            "book_id": f"TBPDF_{hashlib.sha1(identity).hexdigest()[:16]}",
            "title": title,
            "aliases": aliases_for(title),
            "edition": edition,
            "relative_path": relative.as_posix(),
            "size_bytes": path.stat().st_size,
            "sha256": file_sha256(path),
        })
    return {"catalog_version": "1.0.0", "books": books}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf_root", type=Path)
    parser.add_argument("catalog_path", type=Path)
    args = parser.parse_args()
    payload = build_catalog(args.pdf_root.resolve())
    args.catalog_path.parent.mkdir(parents=True, exist_ok=True)
    args.catalog_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"catalogued {len(payload['books'])} PDFs -> {args.catalog_path}")


if __name__ == "__main__":
    main()
