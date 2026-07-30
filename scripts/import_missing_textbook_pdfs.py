from __future__ import annotations

import argparse
import ast
import json
import re
import shutil
from pathlib import Path

from pypdf import PdfReader


def postgraduate_books(knowledge_map_path: Path) -> list[str]:
    module = ast.parse(knowledge_map_path.read_text(encoding="utf-8"))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if any(isinstance(target, ast.Name) and target.id == "POSTGRADUATE_BOOKS" for target in node.targets):
            return list(ast.literal_eval(node.value))
    raise RuntimeError("POSTGRADUATE_BOOKS was not found")


def available_route_books(knowledge_points_path: Path, requested: list[str]) -> list[str]:
    records = json.loads(knowledge_points_path.read_text(encoding="utf-8-sig"))
    available = {
        str((record.get("kp", record)).get("kp_lv1", "")).strip()
        for record in records
    }
    return [title for title in requested if title in available]


def catalog_names(catalog_path: Path) -> set[str]:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for book in catalog.get("books", []):
        names.add(str(book.get("title", "")).strip())
        names.update(str(alias).strip() for alias in book.get("aliases", []))
    return {name for name in names if name}


def normalized_source_title(path: Path) -> str:
    title = re.sub(r"^\d+[-_\s]*", "", path.stem).strip()
    title = re.sub(r"[（(]全国中医药行业高等教育.*?[）)]", "", title).strip()
    title = re.sub(r"[（(]\d+[）)]$", "", title).strip()
    return re.sub(r"\s+", "", title)


def page_count(path: Path) -> int:
    reader = PdfReader(path, strict=False)
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise RuntimeError(f"encrypted PDF cannot be opened: {path}")
    count = len(reader.pages)
    if count <= 0:
        raise RuntimeError(f"PDF has no pages: {path}")
    return count


def choose_source(book: str, files: list[Path]) -> Path | None:
    matches = [path for path in files if normalized_source_title(path) == book]
    if not matches:
        return None
    matches.sort(key=lambda path: (bool(re.search(r"[（(]\d+[）)]$", path.stem)), len(path.name), path.name))
    return matches[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Import currently missing textbook PDFs from a preferred full-PDF folder.")
    parser.add_argument("--knowledge-map", type=Path, required=True)
    parser.add_argument("--knowledge-points", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--source-full", type=Path, required=True)
    parser.add_argument("--pdf-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    requested = postgraduate_books(args.knowledge_map)
    route_books = available_route_books(args.knowledge_points, requested)
    existing = catalog_names(args.catalog)
    missing = [book for book in route_books if book not in existing]
    source_files = sorted(args.source_full.glob("*.pdf"))
    target_dir = args.pdf_root / "十四五"
    target_dir.mkdir(parents=True, exist_ok=True)

    imported: list[dict[str, object]] = []
    unresolved: list[str] = []
    for book in missing:
        source = choose_source(book, source_files)
        if source is None:
            unresolved.append(book)
            continue
        source_pages = page_count(source)
        target = target_dir / f"{book}.pdf"
        temporary = target.with_suffix(".pdf.part")
        shutil.copyfile(source, temporary)
        temporary.replace(target)
        target_pages = page_count(target)
        if target_pages != source_pages:
            raise RuntimeError(f"page count changed while copying {book}: {source_pages} -> {target_pages}")
        imported.append({
            "book": book,
            "edition": "十四五",
            "source": str(source),
            "target": str(target),
            "pages": target_pages,
            "size_bytes": target.stat().st_size,
        })
        print(f"IMPORTED {book}: {target_pages} pages")

    payload = {
        "requested_missing_count": len(missing),
        "imported_count": len(imported),
        "unresolved_count": len(unresolved),
        "imported": imported,
        "unresolved": unresolved,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"RESULT imported={len(imported)} unresolved={len(unresolved)} report={args.report}")


if __name__ == "__main__":
    main()
