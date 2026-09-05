from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tarfile

import pytest


SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "package_public_assets.py"
SPEC = importlib.util.spec_from_file_location("public_asset_packaging", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
packaging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packaging)


def test_package_records_checksums_and_excludes_state(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "reference.json").write_text('{"public": true}', encoding="utf-8")
    (source / ".env.local").write_text("not-for-publication", encoding="utf-8")
    (source / "users.db").write_bytes(b"private")
    (source / "runtime").mkdir()
    (source / "runtime" / "session.json").write_text("private", encoding="utf-8")
    output = tmp_path / "out"
    output.mkdir()
    result = packaging.package(output, "sample", [(source, "assets/sample")])
    assert result["file_count"] == 1
    archive = output / result["file"]
    assert packaging.sha256(archive) == result["sha256"]
    with tarfile.open(archive) as stream:
        assert stream.getnames() == ["assets/sample/reference.json"]
    item = json.loads((output / result["manifest"]).read_text())
    assert item["sha256"] == packaging.sha256(source / "reference.json")


def test_reject_nested_symlink(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "escape").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        list(packaging.files(source))


def test_do_not_overwrite_finished_package(tmp_path):
    (tmp_path / "sample.tar.gz").write_bytes(b"keep")
    with pytest.raises(FileExistsError):
        packaging.package(tmp_path, "sample", [])
    assert (tmp_path / "sample.tar.gz").read_bytes() == b"keep"