import sys
import os
import subprocess
from types import SimpleNamespace
from pathlib import Path

import httpx
import pytest

from competition_app.llm.upload_provider import upload_provider_headers
from competition_app.tools.upload_pipeline_runner import child_command, install_provider_clients


def test_generated_paths_are_portable_and_other_config_is_unchanged(tmp_path):
    from competition_app.tools.upload_pipeline_runner import install_pipeline_paths

    paths = {
        "input_chunks_glob": r"..\02_raw_chunks\*_identifier_chunks.jsonl",
        "outputs_dir": r"..\04_knowledge_points\intermediate",
        "prompts_dir": "prompts",
        "final_output_dir": r"..\04_knowledge_points\final_output",
        "unrelated": r"leave\unchanged",
    }
    original = {"paths": paths, "api": {"base_url": "https://example.com/v1"},
                "prompt": r"keep\ntext"}
    calls = []

    def build(config, delivery):
        calls.append((config, delivery))
        return original

    module = SimpleNamespace(build_kp_config=build)
    install_pipeline_paths(module)
    config = module.build_kp_config("input", tmp_path)
    assert calls == [("input", tmp_path)]
    assert config["api"] is original["api"]
    assert config["prompt"] == original["prompt"]
    assert config["paths"]["unrelated"] == paths["unrelated"]
    assert paths["outputs_dir"] == r"..\04_knowledge_points\intermediate"
    assert config["paths"]["input_chunks_glob"] == "../02_raw_chunks/*_identifier_chunks.jsonl"
    assert config["paths"]["outputs_dir"] == "../04_knowledge_points/intermediate"
    assert config["paths"]["final_output_dir"] == "../04_knowledge_points/final_output"
    assert config["paths"]["prompts_dir"] == "prompts"


def test_generated_paths_work_in_real_delivered_child(tmp_path):
    from competition_app.tools import upload_pipeline_runner

    (tmp_path / "run_pdf_pipeline.py").write_text(
        "import subprocess\n"
        "def run_cmd(command, cwd, env):\n"
        "    subprocess.run(command, cwd=cwd, env=env, check=True)\n"
        "def build_kp_config(config, delivery):\n"
        "    return {'paths': {'input_chunks_glob': r'..\\02_raw_chunks\\*.jsonl', "
        "'outputs_dir': r'..\\04_knowledge_points\\intermediate', "
        "'final_output_dir': r'..\\04_knowledge_points\\final_output', 'prompts_dir': 'prompts'}}\n"
    )
    scripts = tmp_path / "delivery" / "06_scripts"
    scripts.mkdir(parents=True)
    chunks = scripts.parent / "02_raw_chunks"
    chunks.mkdir()
    (chunks / "test.jsonl").write_text('{}\n')
    (scripts / "child.py").write_text(
        "import json, glob\nfrom pathlib import Path\n"
        "cfg=json.loads(Path('config.json').read_text())['paths']\n"
        "assert len(glob.glob(cfg['input_chunks_glob'])) == 1\n"
        "for key in ('outputs_dir', 'final_output_dir'):\n"
        "    out=Path(cfg[key]); out.mkdir(parents=True, exist_ok=True)\n"
        "    (out/'ok.txt').write_text('ok')\n"
    )
    target = tmp_path / "ingest_content.py"
    target.write_text(
        "import json, os, sys, run_pdf_pipeline\nfrom pathlib import Path\n"
        "scripts=Path('delivery/06_scripts').resolve()\n"
        "cfg=run_pdf_pipeline.build_kp_config({}, scripts.parent)\n"
        "(scripts/'config.json').write_text(json.dumps(cfg))\n"
        "run_pdf_pipeline.run_cmd([sys.executable, 'child.py'], scripts, os.environ.copy())\n"
    )
    completed = subprocess.run([sys.executable, upload_pipeline_runner.__file__, str(target)],
        cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    for directory in ("intermediate", "final_output"):
        assert (scripts.parent / "04_knowledge_points" / directory / "ok.txt").read_text() == "ok"


def test_headers_are_provider_scoped():
    assert upload_provider_headers("https://opencode.ai/zen/go/v1", "job-1")["x-opencode-session"] == "job-1"
    assert upload_provider_headers("https://api.siliconflow.cn/v1", "job-1") == {}
    assert upload_provider_headers("https://opencode.ai.example/v1", "job-1") == {}


def test_managed_entry_uses_high_water_in_real_subprocess(tmp_path):
    import json
    from competition_app.tools import upload_pipeline_runner
    for directory in ('stage', 'delivery'):
        (tmp_path / directory / '04_knowledge_points').mkdir(parents=True)
    (tmp_path / 'delivery/09_ingestion').mkdir()
    (tmp_path / 'delivery/09_ingestion/kp_id_high_water.json').write_text('42')
    (tmp_path / 'delivery/04_knowledge_points/final_knowledge_points.json').write_text('[]')
    for name in ('final_knowledge_points.json', 'final_knowledge_points.with_meta.json'):
        (tmp_path / 'stage/04_knowledge_points' / name).write_text(json.dumps([{'kp_id': '1', 'order_code': 'old'}]))
    target = tmp_path / 'ingest_content.py'
    target.write_text(
        "from pathlib import Path\nfrom dataclasses import dataclass\n"
        "@dataclass\nclass Source:\n    name: str\n"
        "ORDER_FIELDS = {'order_code'}\n"
        "def reserve_kp_ids(*args):\n    raise RuntimeError('unadapted allocator')\n"
        "def main():\n    assert reserve_kp_ids(Path('stage'), Path('delivery')) == {'1': '000043'}\n"
        "if __name__ == '__main__':\n    main()\n"
    )
    completed = subprocess.run([sys.executable, upload_pipeline_runner.__file__, str(target)], cwd=tmp_path,
        env={**os.environ, 'COMPETITION_PERSONAL_MANAGEMENT': '1'}, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert json.loads((tmp_path / 'delivery/09_ingestion/kp_id_high_water.json').read_text()) == 43


def test_child_runner_wraps_python_scripts_only():
    args = [sys.executable, "run_fast_pipeline.py", "--config", "config.json", "all"]
    wrapped = child_command(args)
    assert Path(wrapped[1]).name == "upload_pipeline_runner.py"
    assert wrapped[2:] == args[1:]
    assert child_command(wrapped) == wrapped
    assert child_command(["curl", "https://example.com"]) == ["curl", "https://example.com"]


@pytest.mark.asyncio
async def test_sdk_clients_inject_session_without_affecting_embedding_provider(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "OpenAI", openai.OpenAI)
    monkeypatch.setattr(openai, "AsyncOpenAI", openai.AsyncOpenAI)
    install_provider_clients("upload-test-session")
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"id": "test", "object": "chat.completion",
            "created": 0, "model": "test", "choices": [{"index": 0,
            "message": {"role": "assistant", "content": "{}"}, "finish_reason": "stop"}]})

    with openai.OpenAI(api_key="test", base_url="https://opencode.ai/zen/go/v1",
                       http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
        client.chat.completions.create(model="test", messages=[])
    async with openai.AsyncOpenAI(api_key="test", base_url="https://opencode.ai/zen/go/v1",
                       http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond))) as client:
        await client.chat.completions.create(model="test", messages=[])
    with openai.OpenAI(api_key="test", base_url="https://api.siliconflow.cn/v1",
                       http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
        client.chat.completions.create(model="test", messages=[])
    assert requests[0].headers["x-opencode-session"] == "upload-test-session"
    assert requests[1].headers["x-opencode-session"] == "upload-test-session"
    assert "x-opencode-session" not in requests[2].headers


def test_runner_propagates_session_to_delivered_python_child(tmp_path):
    from competition_app.tools import upload_pipeline_runner

    (tmp_path / "run_pdf_pipeline.py").write_text(
        "import subprocess\ndef run_cmd(command, cwd, env):\n"
        "    subprocess.run(command, cwd=cwd, env=env, check=True)\n"
    )
    (tmp_path / "child.py").write_text(
        "import openai, os\n"
        "client = openai.OpenAI(api_key='test', base_url='https://opencode.ai/zen/go/v1')\n"
        "assert client.default_headers['x-opencode-session'] == os.environ['COMPETITION_UPLOAD_SESSION']\n"
        "print('child-session-ok')\n"
    )
    target = tmp_path / "ingest_content.py"
    target.write_text(
        "import sys, os, run_pdf_pipeline\n"
        "run_pdf_pipeline.run_cmd([sys.executable, 'child.py'], os.getcwd(), os.environ.copy())\n"
    )
    completed = subprocess.run([sys.executable, upload_pipeline_runner.__file__, str(target)],
        cwd=tmp_path, env={**os.environ, "COMPETITION_UPLOAD_SESSION": "test-job"},
        capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert "child-session-ok" in completed.stdout


def test_pipeline_errors_do_not_expose_child_stderr(tmp_path, caplog):
    from competition_app.tools.knowledge_delivery import _pipeline_failure

    error = _pipeline_failure(tmp_path / "run-id", SimpleNamespace(
        returncode=1, stderr="secret-token /srv/private traceback", stdout="private document"
    ), "资料解析")
    assert "run-id" in str(error)
    assert "secret-token" not in str(error) + caplog.text
    assert "/srv/private" not in str(error) + caplog.text


def test_chapter_hierarchy_uses_configured_external_script(tmp_path, monkeypatch):
    from competition_app.tools.knowledge_delivery import KnowledgeDeliveryBackend
    import json

    root = tmp_path / "external-chapters"
    root.mkdir()
    (root / "chapter_hierarchy.py").write_text("# external script")
    delivery = tmp_path / "delivery"
    chunks = delivery / "03_pipeline_chunks"
    chunks.mkdir(parents=True)
    (chunks / "source_chunks.jsonl").write_text("{}\n")
    run = tmp_path / "run"
    (run / "normalized_books").mkdir(parents=True)
    monkeypatch.setenv("KNOWLEDGE_ATLAS_CHAPTER_ROOT", str(root))

    def execute(command, **kwargs):
        assert command[1] == str(root / "chapter_hierarchy.py")
        (chunks / "chapter_hierarchy_report.json").write_text(json.dumps({"ok": True, "chapter_nodes": 1}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("competition_app.tools.knowledge_delivery.subprocess.run", execute)
    backend = object.__new__(KnowledgeDeliveryBackend)
    result = backend._sync_user_chapter_hierarchy(run_dir=run, delivery=delivery, ingestion_id="test")
    assert result["ok"] is True