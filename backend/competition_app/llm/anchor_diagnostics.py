"""Bounded, text-free evidence for the fixed-route anchor gate.

These observations NEVER authorize a contract or repair a model response.
Offsets refer to the original strings. Normalized comparisons are diagnostic
only; they must not be used by the business validator.
"""

import hashlib
import re
from typing import Any

from competition_app.contracts.route_binding import quote_within_stage


def _fingerprint(value: Any) -> dict[str, Any]:
    if not isinstance(value, str):
        return {"is_string": False}
    return {
        "is_string": True, "chars": len(value),
        "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
    }


def stage_anchor_diagnostics(
    document: str, stage: dict[str, Any], trusted: dict[str, Any] | None,
    stages: list[dict[str, Any]], entries: Any, last_position: int,
    index: int,
) -> dict[str, Any]:
    """Observe the gate's individual conditions without echoing model text."""
    stage_id = str(stage.get("stage_id") or "")
    summary = str(stage.get("schedule_summary") or "")
    duration = stage.get("duration_days")
    books = trusted["books"] if trusted else []
    result = {
        "version": 1, "field_path": f"/stages/{index}/stage_id",
        "document": _fingerprint(document), "stage_id": _fingerprint(stage_id),
        "summary": _fingerprint(summary), "trusted_stage_found": trusted is not None,
        "last_position": last_position, "entries_is_list": isinstance(entries, list),
        "entry_count": len(entries) if isinstance(entries, list) else 0,
        "summary_position_in_document": document.find(summary),
        "entries": [],
    }
    # Fingerprint a bounded slice only after declaring truncation. This limit
    # applies to analysis work, not to the original production gate.
    bounded_document = document[:20000]
    for entry_index, entry in enumerate(entries[:8] if isinstance(entries, list) else []):
        quote = entry.get("source_quote") if isinstance(entry, dict) else None
        valid_quote = isinstance(quote, str) and bool(quote)
        source_ok = isinstance(entry, dict) and entry.get("source_field") == "plan_document"
        detail = {
            "index": entry_index, "is_object": isinstance(entry, dict),
            "quote": _fingerprint(quote), "source_field_is_plan_document": source_ok,
            "quote_nonempty": valid_quote,
        }
        result["entries"].append(detail)
        if not valid_quote:
            continue
        position = document.find(quote)
        checks = {
            "source_field": source_ok,
            "trusted_stage": trusted is not None,
            "quote_in_document": position >= 0,
            "ordered_after_previous": position >= last_position,
            "stage_id_in_quote": re.search(rf"(?<![\w-]){re.escape(stage_id)}(?![\w-])", quote) is not None,
            "all_books_in_quote": all(str(book) in quote for book in books),
            "summary_in_quote": summary in quote,
            "duration_in_quote": re.search(rf"(?<!\d){duration}(?!\d)", quote) is not None,
            "no_other_stage_id": not any(
                re.search(rf"(?<![\w-]){re.escape(other['stage_id'])}(?![\w-])", quote)
                for other in stages if other["stage_id"] != stage_id
            ),
            "within_stage_section": quote_within_stage(document, quote, stage_id, stages),
        }
        detail.update({
            "checks": checks, "gate_would_accept": all(value for key, value in checks.items() if key != "no_other_stage_id"),
            "quote_position": position, "quote_end": position + len(quote) if position >= 0 else None,
            "summary_position_in_quote": quote.find(summary),
            "missing_book_indexes": [i for i, book in enumerate(books) if str(book) not in quote][:32],
        })
        if position < 0:
            bounded_quote = quote[:12000]
            complete = len(document) <= 20000 and len(quote) <= 12000
            # Text-free difference classification, never a fuzzy acceptance.
            compact = lambda text: re.sub(r"\s+", "", text)
            plain = lambda text: re.sub(r"[*_`#]", "", text)
            detail["difference"] = {
                "analysis_complete": complete,
                "whitespace_only_match": complete and bool(compact(bounded_quote)) and compact(bounded_quote) in compact(bounded_document),
                "markdown_markers_only_match": complete and bool(plain(bounded_quote)) and plain(bounded_quote) in plain(bounded_document),
                "whitespace_and_markers_match": complete and bool(compact(plain(bounded_quote))) and compact(plain(bounded_quote)) in compact(plain(bounded_document)),
            }
    result["entries_omitted"] = max(0, result["entry_count"] - 8)
    return result