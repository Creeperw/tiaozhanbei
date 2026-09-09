import json
import subprocess
from pathlib import Path

import pytest

from competition_app.services import document_parsing as parsing
from competition_app.services.textbook_import import TextbookImportService
from competition_app.services.user_syllabus import UserSyllabusService


@pytest.fixture
def pipeline(tmp_path):
    root = tmp_path / "pipeline"
    root.mkdir()
    (root / "parse_question_pdf.py").write_text("# offline fixture")
    (root / "pipeline_config.json").write_text("{}")
    source = tmp_path / "source.pdf"
    source.write_bytes(b"%PDF-test")
    return root, source, tmp_path / "output"


def install_run(monkeypatch, returncodes=(0,), diagnostic=""):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        output = Path(command[command.index("--output-dir") + 1])
        (output / "part001_clean.md").write_text("\ufeff正文一", encoding="utf-8")
        (output / "part002_clean.md").write_text("正文二", encoding="utf-8")
        (output / "raw.md").write_text("原始副本", encoding="utf-8")
        return subprocess.CompletedProcess(command, returncodes[min(len(calls)-1, len(returncodes)-1)], "", diagnostic)

    monkeypatch.setattr(parsing.subprocess, "run", run)
    monkeypatch.setattr(parsing.time, "sleep", lambda _: None)
    return calls


def test_clean_selection_resume_and_order(pipeline, monkeypatch):
    root, source, output = pipeline
    output.mkdir()
    combined = output / "textbook_full_clean.md"
    combined.write_text("不应再次拼接的上次聚合结果")
    calls = install_run(monkeypatch)
    result = parsing.parse_mineru([source, source], pipeline_root=root, token="server-token",
                                 output_dir=output, exclude_markdown=(combined,))
    assert result.markdown == "正文一\n\n正文二"
    assert len(result.markdown_files) == 2
    assert result.parser == "mineru" and result.run_id
    command, options = calls[0]
    assert command.count("--pdf") == 2
    assert options["env"]["MINERU_TOKEN"] == "server-token"
    assert "server-token" not in command
    assert options["cwd"] == root


def test_formal_adapter_all_markdown_policy(pipeline, monkeypatch):
    root, source, output = pipeline
    install_run(monkeypatch)
    result = parsing.parse_mineru([source], pipeline_root=root, token="t", output_dir=output,
                                 prefer_clean=False)
    assert "原始副本" in result.markdown


def test_retry_only_fixed_download_diagnostic(pipeline, monkeypatch):
    root, source, output = pipeline
    calls = install_run(monkeypatch, (1, 0), "download failed after retries: secret")
    assert parsing.parse_mineru([source], pipeline_root=root, token="t", output_dir=output,
                               attempts=3).markdown
    assert len(calls) == 2


def test_failure_does_not_leak_or_read_stale_artifacts(pipeline, monkeypatch):
    root, source, output = pipeline
    calls = install_run(monkeypatch, (1,), "secret-token private-document")
    with pytest.raises(parsing.DocumentParseError) as caught:
        parsing.parse_mineru([source], pipeline_root=root, token="t", output_dir=output, attempts=3)
    assert len(calls) == 1
    assert "secret" not in str(caught.value) and "private" not in str(caught.value)


def test_timeout_is_safe(pipeline, monkeypatch):
    root, source, output = pipeline
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired("secret-command", 1, stderr="private")
    monkeypatch.setattr(parsing.subprocess, "run", run)
    with pytest.raises(parsing.DocumentParseError, match="超时") as caught:
        parsing.parse_mineru([source], pipeline_root=root, token="t", output_dir=output)
    assert "secret" not in str(caught.value)


def test_missing_output_and_external_symlink_rejected(pipeline, monkeypatch, tmp_path):
    root, source, output = pipeline
    monkeypatch.setattr(parsing.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 0))
    with pytest.raises(parsing.DocumentParseError, match="未生成"):
        parsing.parse_mineru([source], pipeline_root=root, token="t", output_dir=output)
    outside = tmp_path / "private.md"
    outside.write_text("private")
    (output / "bad_clean.md").symlink_to(outside)
    with pytest.raises(parsing.DocumentParseError, match="无法读取"):
        parsing.parse_mineru([source], pipeline_root=root, token="t", output_dir=output)


def test_adapters_preserve_page_map_and_exclude_aggregate_on_resume(pipeline, monkeypatch):
    root, source, output = pipeline
    install_run(monkeypatch)
    kwargs = dict(chat_base_url="https://example.test/v1", chat_model="test", chat_api_key="t",
                  mineru_token="t", mineru_pipeline_root=root)
    textbook = TextbookImportService(runtime_root=output.parent, **kwargs)
    monkeypatch.setattr(textbook, "_split_for_mineru", lambda *a: [source])
    monkeypatch.setattr(textbook, "_mineru_page_text", lambda *a: {3: "正文一", 4: "正文二"})
    for _ in range(2):
        markdown, pages = textbook._run_mineru(source, output / "textbook")
        assert markdown.read_text() == "正文一\n\n正文二"
        assert pages == {3: "正文一", 4: "正文二"}
    syllabus = UserSyllabusService(output.parent, **kwargs)
    for _ in range(2):
        assert syllabus._mineru_markdown(source, output / "syllabus").read_text() == "正文一\n\n正文二"


def test_real_content_list_page_offsets_survive_shared_executor(pipeline, monkeypatch):
    root, source, output = pipeline
    service = TextbookImportService(runtime_root=output.parent,
        chat_base_url="https://example.test/v1", chat_model="test", chat_api_key="t",
        mineru_token="t", mineru_pipeline_root=root)
    monkeypatch.setattr(service, "_split_for_mineru", lambda *a: [source])

    def run(command, **kwargs):
        target = Path(command[command.index("--output-dir") + 1])
        for part, text in [("pages_0001-0200", "第一页"), ("pages_0201-0400", "第201页")]:
            folder = target / part
            folder.mkdir(parents=True)
            (folder / "book_clean.md").write_text(text)
            (folder / "book_content_list.json").write_text(json.dumps([
                {"page_idx": 0, "text": text},
            ]))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(parsing.subprocess, "run", run)
    markdown, pages = service._run_mineru(source, output)
    assert pages == {1: "第一页", 201: "第201页"}
    assert markdown.read_text() == "第一页\n\n第201页"