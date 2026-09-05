import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Mapping


FORMAL_DELIVERY_FILES = (
    "knowledge_points.jsonl",
    "questions.jsonl",
    "question_versions.jsonl",
    "question_links.jsonl",
    "knowledge_point_versions.jsonl",
    "knowledge_point_links.jsonl",
    "media.jsonl",
    "manifest.json",
)
DEFAULT_MAX_RECORD_BYTES = 1_048_576
DEFAULT_MAX_JSON_DEPTH = 16
_HASH_CHUNK_BYTES = 64 * 1024


class DeliveryImportError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid-record") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class KnowledgePointDTO:
    kp_id: str
    name: str


@dataclass(frozen=True)
class QuestionDTO:
    question_id: str
    question_type: str


@dataclass(frozen=True)
class QuestionVersionDTO:
    question_version_id: str
    question_id: str
    data_version: str


@dataclass(frozen=True)
class QuestionLinkDTO:
    question_id: str
    kp_id: str


@dataclass(frozen=True)
class DeliveryValidationResult:
    valid: bool
    status: str
    missing_files: tuple[str, ...] = ()
    data_version: str | None = None
    sha256: str | None = None
    record_counts: tuple[tuple[str, int], ...] = ()
    error_code: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class DeliveryDryRunResult:
    valid: bool
    status: str
    missing_files: tuple[str, ...]
    data_version: str | None
    sha256: str | None
    record_counts: tuple[tuple[str, int], ...]
    idempotent: bool
    error_code: str | None = None
    reason: str | None = None


def parse_knowledge_point(payload: Mapping[str, object]) -> KnowledgePointDTO:
    return KnowledgePointDTO(kp_id=_required_text(payload, "kp_id"), name=_required_text(payload, "name"))


def parse_question(payload: Mapping[str, object]) -> QuestionDTO:
    return QuestionDTO(
        question_id=_required_text(payload, "question_id"),
        question_type=_required_text(payload, "question_type"),
    )


def parse_question_version(payload: Mapping[str, object]) -> QuestionVersionDTO:
    return QuestionVersionDTO(
        question_version_id=_required_text(payload, "question_version_id"),
        question_id=_required_text(payload, "question_id"),
        data_version=_required_text(payload, "data_version"),
    )


def parse_question_link(payload: Mapping[str, object]) -> QuestionLinkDTO:
    return QuestionLinkDTO(question_id=_required_text(payload, "question_id"), kp_id=_required_text(payload, "kp_id"))


def resolve_storage_path(image_root: str | Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise DeliveryImportError("storage_relative_path must be a non-empty relative path", code="invalid-path")
    requested = Path(relative_path)
    if requested.is_absolute() or ".." in requested.parts:
        raise DeliveryImportError("storage_relative_path must stay within image_root", code="invalid-path")
    root = Path(image_root).resolve()
    resolved = (root / requested).resolve()
    _require_contained(root, resolved)
    return resolved


def iter_jsonl_records(
    path: str | Path,
    *,
    max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    max_depth: int = DEFAULT_MAX_JSON_DEPTH,
) -> Iterator[dict[str, object]]:
    if max_record_bytes <= 0 or max_depth < 0:
        raise ValueError("JSON limits must be positive")
    with Path(path).open("rb") as source:
        line_number = 0
        while chunk := source.readline(max_record_bytes + 1):
            line_number += 1
            if len(chunk) > max_record_bytes or (not chunk.endswith(b"\n") and source.peek(1)):
                raise DeliveryImportError(f"JSONL record {line_number} exceeds size limit", code="invalid-size")
            if not chunk.strip():
                continue
            try:
                record = json.loads(chunk)
            except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
                raise DeliveryImportError(f"invalid JSONL record {line_number}", code="invalid-json") from error
            if not isinstance(record, dict):
                raise DeliveryImportError(f"JSONL record {line_number} must be an object")
            _validate_json_depth(record, max_depth)
            yield record


def validate_delivery_batch(delivery_root: str | Path) -> DeliveryValidationResult:
    try:
        root, files = _resolve_delivery_files(delivery_root)
    except DeliveryImportError as error:
        return _invalid_validation(error.code)

    missing_files = tuple(name for name, path in files.items() if not path.is_file())
    if missing_files:
        return DeliveryValidationResult(
            False,
            "missing-files",
            missing_files,
            error_code="missing-files",
            reason="missing-required-files",
        )

    try:
        manifest = _parse_manifest(files["manifest.json"])
        data_version = _required_text(manifest, "data_version")
        counts = []
        for name, path in files.items():
            if name == "manifest.json":
                continue
            count = 0
            for record in iter_jsonl_records(path):
                _validate_delivery_record(name, record)
                count += 1
            counts.append((name, count))
        return DeliveryValidationResult(
            True,
            "valid",
            data_version=data_version,
            sha256=_batch_sha256(files),
            record_counts=tuple(counts),
        )
    except DeliveryImportError as error:
        return _invalid_validation(error.code)
    except (OSError, UnicodeDecodeError):
        return _invalid_validation("invalid-io")


def dry_run_delivery_import(
    delivery_root: str | Path,
    *,
    known_imports: set[tuple[str, str]] | None = None,
    database_writer: Callable[[object], None] | None = None,
) -> DeliveryDryRunResult:
    del database_writer
    validation = validate_delivery_batch(delivery_root)
    if not validation.valid:
        return DeliveryDryRunResult(
            False,
            validation.status,
            validation.missing_files,
            None,
            None,
            (),
            False,
            validation.error_code,
            validation.reason,
        )
    import_key = (validation.data_version, validation.sha256)
    return DeliveryDryRunResult(
        True,
        "valid",
        (),
        validation.data_version,
        validation.sha256,
        validation.record_counts,
        import_key in (known_imports or set()),
    )


def _resolve_delivery_files(delivery_root: str | Path) -> tuple[Path, dict[str, Path]]:
    requested_root = Path(delivery_root)
    if requested_root.is_symlink():
        raise DeliveryImportError("delivery root must not be a symlink", code="invalid-path")
    root = requested_root.resolve()
    files = {}
    for name in FORMAL_DELIVERY_FILES:
        resolved = (root / name).resolve()
        _require_contained(root, resolved)
        files[name] = resolved
    return root, files


def _require_contained(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise DeliveryImportError("delivery file escapes delivery root", code="invalid-path") from error


def _parse_manifest(path: Path) -> dict[str, object]:
    raw = _read_bounded_bytes(path, DEFAULT_MAX_RECORD_BYTES)
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise DeliveryImportError("invalid manifest", code="invalid-structure") from error
    if not isinstance(manifest, dict):
        raise DeliveryImportError("manifest must be a JSON object", code="invalid-structure")
    _validate_json_depth(manifest, DEFAULT_MAX_JSON_DEPTH)
    return manifest


def _read_bounded_bytes(path: Path, limit: int) -> bytes:
    with path.open("rb") as source:
        content = source.read(limit + 1)
    if len(content) > limit:
        raise DeliveryImportError("file exceeds size limit", code="invalid-size")
    return content


def _validate_delivery_record(name: str, record: Mapping[str, object]) -> None:
    if name == "knowledge_points.jsonl":
        parse_knowledge_point(record)
    elif name == "questions.jsonl":
        parse_question(record)
    elif name == "question_versions.jsonl":
        parse_question_version(record)
    elif name == "question_links.jsonl":
        parse_question_link(record)
    elif name == "knowledge_point_versions.jsonl":
        _require_fields(record, "kp_id", "data_version")
    elif name == "knowledge_point_links.jsonl":
        _require_fields(record, "kp_id")
    elif name == "media.jsonl":
        _require_fields(record, "media_id")


def _require_fields(payload: Mapping[str, object], *keys: str) -> None:
    for key in keys:
        _required_text(payload, key)


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DeliveryImportError(f"{key} must be a non-empty string")
    return value


def _validate_json_depth(value: object, max_depth: int) -> None:
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > max_depth:
            raise DeliveryImportError("JSON value exceeds structural limits", code="invalid-structure")
        if isinstance(current, dict):
            stack.extend((item, depth + 1) for item in current.values())
        elif isinstance(current, list):
            stack.extend((item, depth + 1) for item in current)


def _batch_sha256(files: Mapping[str, Path]) -> str:
    digest = hashlib.sha256()
    for name in FORMAL_DELIVERY_FILES:
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        with files[name].open("rb") as source:
            while chunk := source.read(_HASH_CHUNK_BYTES):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def _invalid_validation(code: str) -> DeliveryValidationResult:
    reasons = {
        "missing-files": "missing-required-files",
        "invalid-path": "path-outside-root",
        "invalid-size": "record-too-large",
        "invalid-json": "invalid-json-record",
        "invalid-record": "invalid-record-schema",
        "invalid-structure": "invalid-manifest",
    }
    return DeliveryValidationResult(False, code, error_code=code, reason=reasons.get(code, "invalid-batch"))
