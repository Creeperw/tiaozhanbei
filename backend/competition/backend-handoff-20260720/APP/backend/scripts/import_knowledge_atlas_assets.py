from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import shutil
import time
from pathlib import Path
from typing import Any, Iterable


ASSET_VERSION = "2026-07-18"
CONTRACT_SCHEMA_VERSION = "1.0.0"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACKAGE_ROOT = (
    REPOSITORY_ROOT / "division of labor" / "知识星球视频知识库_前端交接包_2026-07-18"
)
DEFAULT_DATA_ROOT = BACKEND_ROOT / "knowledge_atlas_assets" / ASSET_VERSION / "backend_delivery"
DEFAULT_VIDEO_ROOT = BACKEND_ROOT / "knowledge_atlas_runtime" / "video"
DEFAULT_CONTRACT_PATH = BACKEND_ROOT / "knowledge_atlas_contracts" / ASSET_VERSION / "manifest.json"


class KnowledgeAtlasImportError(RuntimeError):
    """Base error for a safe Knowledge Atlas asset import."""


class SourceAssetMissingError(KnowledgeAtlasImportError):
    """Raised when the immutable handoff source is unavailable."""


class ContractVerificationError(KnowledgeAtlasImportError):
    """Raised when a source or copied tree does not match the tracked contract."""


class AssetConflictError(KnowledgeAtlasImportError):
    """Raised when a destination exists but differs from the tracked contract."""


class AtomicPromotionError(KnowledgeAtlasImportError):
    """Raised when a verified staging tree cannot be promoted into place."""


def _iter_tree_files(root: Path) -> Iterable[tuple[str, Path]]:
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise ContractVerificationError(f"资产树不允许符号链接: {path}")
        if path.is_file():
            yield path.relative_to(root).as_posix(), path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_component_inventory(root: str | Path, sample_files: Iterable[str] = ()) -> dict[str, Any]:
    """Return a deterministic, content-addressed inventory for one component tree."""

    tree_root = Path(root)
    if not tree_root.is_dir():
        raise SourceAssetMissingError(f"知识星球资产目录不存在: {tree_root}")

    manifest_digest = hashlib.sha256()
    total_bytes = 0
    file_count = 0
    file_digests: dict[str, str] = {}
    for relative_path, file_path in _iter_tree_files(tree_root):
        size = file_path.stat().st_size
        digest = _sha256_file(file_path)
        manifest_digest.update(f"{relative_path}\0{size}\0{digest}\n".encode("utf-8"))
        total_bytes += size
        file_count += 1
        file_digests[relative_path] = digest

    samples: dict[str, str] = {}
    for sample in sample_files:
        normalized = Path(sample).as_posix()
        if normalized not in file_digests:
            raise ContractVerificationError(f"抽样文件不存在: {normalized} ({tree_root})")
        samples[normalized] = file_digests[normalized]

    return {
        "file_count": file_count,
        "total_bytes": total_bytes,
        "tree_sha256": manifest_digest.hexdigest(),
        "sample_sha256": samples,
    }


def _load_contract(contract_path: Path) -> dict[str, Any]:
    if not contract_path.is_file():
        raise SourceAssetMissingError(f"知识星球资产合约不存在: {contract_path}")
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractVerificationError(f"无法读取知识星球资产合约: {contract_path}") from exc
    if contract.get("schema_version") != CONTRACT_SCHEMA_VERSION:
        raise ContractVerificationError(
            f"不支持的资产合约版本: {contract.get('schema_version')!r}"
        )
    if contract.get("asset_version") != ASSET_VERSION:
        raise ContractVerificationError(f"资产版本不匹配: {contract.get('asset_version')!r}")
    components = contract.get("components")
    if not isinstance(components, list) or not components:
        raise ContractVerificationError("资产合约缺少 components")
    return contract


def _expected_inventory(component: dict[str, Any]) -> dict[str, Any]:
    required = ("file_count", "total_bytes", "tree_sha256", "sample_sha256")
    missing = [key for key in required if key not in component]
    if missing:
        raise ContractVerificationError(
            f"组件 {component.get('name', '<unknown>')} 合约缺少字段: {', '.join(missing)}"
        )
    return {key: component[key] for key in required}


def _verify_component(root: Path, component: dict[str, Any], *, conflict: bool) -> dict[str, Any]:
    name = str(component.get("name") or "<unknown>")
    actual = build_component_inventory(root, component.get("sample_files") or [])
    expected = _expected_inventory(component)
    if actual != expected:
        error_type = AssetConflictError if conflict else ContractVerificationError
        raise error_type(
            f"组件 {name} 清单不匹配；拒绝{'覆盖目标' if conflict else '导入'}: "
            f"expected={expected}, actual={actual}"
        )
    return actual


def _resolve_target(component: dict[str, Any], data_root: Path, video_root: Path) -> Path:
    root_name = component.get("root")
    target_name = component.get("target")
    if root_name not in {"data", "video"} or not isinstance(target_name, str) or not target_name:
        raise ContractVerificationError(f"组件目标配置无效: {component.get('name', '<unknown>')}")
    if root_name == "data":
        # KNOWLEDGE_ATLAS_DATA_ROOT is the final backend_delivery path consumed by
        # the service, not its parent. This keeps CLI and runtime config semantics equal.
        return data_root.resolve()
    base = video_root
    target = (base / target_name).resolve()
    if target.parent != base.resolve():
        raise ContractVerificationError(f"组件目标必须是配置根目录的直接子目录: {target_name}")
    return target


def _promote_staging(
    staging: Path,
    target: Path,
    *,
    retry_delays: tuple[float, ...] = (0.25, 1.0, 2.0),
) -> None:
    """Rename a verified directory, tolerating short-lived Windows scanner locks."""

    gc.collect()
    for attempt in range(len(retry_delays) + 1):
        try:
            staging.rename(target)
            return
        except PermissionError:
            if target.exists():
                raise AssetConflictError(f"原子切换期间目标已出现，拒绝覆盖: {target}")
            if attempt == len(retry_delays):
                raise
            time.sleep(retry_delays[attempt])


def ready_receipt_path(target: Path) -> Path:
    return target.parent / f".{target.name}.ready.json"


def _write_ready_receipt(target: Path, component: dict[str, Any], *, mode: str) -> None:
    receipt = ready_receipt_path(target)
    temporary = receipt.with_name(f"{receipt.name}.tmp-{os.getpid()}")
    payload = {
        "schema_version": CONTRACT_SCHEMA_VERSION,
        "asset_version": ASSET_VERSION,
        "component": component["name"],
        "target": target.name,
        "file_count": component["file_count"],
        "total_bytes": component["total_bytes"],
        "tree_sha256": component["tree_sha256"],
        "promotion_mode": mode,
    }
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(receipt)


def _copy_promote_staging(staging: Path, target: Path, component: dict[str, Any]) -> None:
    """Fallback for workspace watchers that deny renaming large directories.

    A sidecar readiness receipt is written only after the final tree passes the same
    content-addressed contract. Atlas services treat an active staging tree without
    the receipt as unavailable, so the copy is a logical atomic switch.
    """

    if target.exists():
        raise AssetConflictError(f"copy promotion 期间目标已出现，拒绝覆盖: {target}")
    try:
        shutil.copytree(staging, target, copy_function=shutil.copy2)
        _verify_component(target, component, conflict=False)
        _write_ready_receipt(target, component, mode="verified-copy")
    except Exception:
        if target.exists() and target.parent == staging.parent:
            shutil.rmtree(target)
        raise
    try:
        shutil.rmtree(staging)
    except OSError:
        # The verified target and receipt are authoritative. A locked, ignored staging
        # tree can be removed on a later idempotent run without affecting availability.
        pass


def import_knowledge_atlas_assets(
    *,
    package_root: str | Path,
    data_root: str | Path,
    video_root: str | Path,
    contract_path: str | Path,
    verify_only: bool = False,
    component_names: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Safely copy an immutable handoff into backend-owned runtime directories.

    All sources and existing targets are verified before the first copy. New trees are
    copied into a private sibling staging directory, verified, then renamed into place.
    Existing targets are never overwritten.
    """

    package = Path(package_root)
    if not package.is_dir():
        raise SourceAssetMissingError(f"知识星球交接包不存在: {package}")
    contract = _load_contract(Path(contract_path))
    data_destination = Path(data_root)
    video_destination = Path(video_root)
    requested = set(component_names or ())
    known = {str(component.get("name")) for component in contract["components"]}
    unknown = requested - known
    if unknown:
        raise ContractVerificationError(f"资产合约中不存在组件: {', '.join(sorted(unknown))}")
    selected_components = [
        component
        for component in contract["components"]
        if not requested or component.get("name") in requested
    ]

    prepared: list[tuple[dict[str, Any], Path, Path]] = []
    for component in selected_components:
        name = str(component.get("name") or "<unknown>")
        source_relative = component.get("source")
        if not isinstance(source_relative, str) or not source_relative:
            raise ContractVerificationError(f"组件 {name} 缺少 source")
        source = package / Path(source_relative)
        if not source.is_dir():
            raise SourceAssetMissingError(f"组件 {name} 的源目录不存在: {source}")
        _verify_component(source, component, conflict=False)
        target = _resolve_target(component, data_destination, video_destination)
        prepared.append((component, source, target))

    for component, _source, target in prepared:
        if target.exists():
            if not target.is_dir():
                raise AssetConflictError(f"组件 {component['name']} 的目标不是目录: {target}")
            _verify_component(target, component, conflict=True)

    if verify_only:
        return {
            "asset_version": contract["asset_version"],
            "verified": len(prepared),
            "copied": 0,
            "skipped": sum(1 for _component, _source, target in prepared if target.exists()),
            "components": [
                {
                    "name": component["name"],
                    "tree_sha256": component["tree_sha256"],
                    "target": str(target),
                }
                for component, _source, target in prepared
            ],
            "verify_only": True,
        }

    copied = 0
    skipped = 0
    component_reports: list[dict[str, Any]] = []
    for component, source, target in prepared:
        if target.exists():
            _write_ready_receipt(target, component, mode="existing-verified")
            skipped += 1
            component_reports.append(
                {
                    "name": component["name"],
                    "tree_sha256": component["tree_sha256"],
                    "target": str(target),
                    "action": "skipped",
                }
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.parent / (
            f".{target.name}.importing-{component['tree_sha256'][:12]}"
        )
        staging_created = False
        staging_verified = False
        try:
            if staging.exists():
                if not staging.is_dir():
                    raise AssetConflictError(
                        f"组件 {component['name']} 的 staging 目标不是目录: {staging}"
                    )
            else:
                shutil.copytree(source, staging, copy_function=shutil.copy2)
                staging_created = True
            _verify_component(staging, component, conflict=False)
            staging_verified = True
            try:
                _promote_staging(staging, target)
                _write_ready_receipt(target, component, mode="rename")
            except PermissionError:
                _copy_promote_staging(staging, target, component)
        except Exception:
            if staging_created and not staging_verified and staging.exists() and staging.parent == target.parent:
                shutil.rmtree(staging)
            raise
        copied += 1
        component_reports.append(
            {
                "name": component["name"],
                "tree_sha256": component["tree_sha256"],
                "target": str(target),
                "action": "copied",
            }
        )

    return {
        "asset_version": contract["asset_version"],
        "verified": len(prepared),
        "copied": copied,
        "skipped": skipped,
        "components": component_reports,
        "verify_only": False,
    }


def _default_from_env(name: str, fallback: Path) -> Path:
    value = os.getenv(name, "").strip()
    return Path(value) if value else fallback


def main() -> int:
    parser = argparse.ArgumentParser(description="幂等导入并校验知识星球交接资产")
    parser.add_argument("--package-root", type=Path, default=DEFAULT_PACKAGE_ROOT)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=_default_from_env("KNOWLEDGE_ATLAS_DATA_ROOT", DEFAULT_DATA_ROOT),
        help="Atlas backend_delivery 最终目录（与 KNOWLEDGE_ATLAS_DATA_ROOT 语义一致）",
    )
    parser.add_argument(
        "--video-root",
        type=Path,
        default=_default_from_env("KNOWLEDGE_ATLAS_VIDEO_ROOT", DEFAULT_VIDEO_ROOT),
    )
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT_PATH)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument(
        "--component",
        action="append",
        dest="components",
        help="只导入指定组件，可重复使用；默认导入全部组件",
    )
    args = parser.parse_args()
    try:
        report = import_knowledge_atlas_assets(
            package_root=args.package_root,
            data_root=args.data_root,
            video_root=args.video_root,
            contract_path=args.contract,
            verify_only=args.verify_only,
            component_names=args.components,
        )
    except KnowledgeAtlasImportError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
