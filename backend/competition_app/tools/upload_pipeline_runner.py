"""Run delivered import scripts with scoped provider compatibility.

Only this subprocess is adapted. Public assets and the host's SDK remain unchanged.
The delivered pipeline's explicit run_cmd hook propagates the same job session to
its Python children; no global sitecustomize or interpreter installation is used.
"""
from __future__ import annotations

import os
from functools import wraps
from pathlib import Path
import runpy
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from competition_app.llm.upload_provider import new_upload_session, upload_provider_headers


def install_provider_clients(session: str) -> None:
    import openai

    def adapted(base):
        class UploadClient(base):
            def __init__(self, *args, **kwargs):
                headers = dict(kwargs.get("default_headers") or {})
                headers.update(upload_provider_headers(str(kwargs.get("base_url") or ""), session))
                kwargs["default_headers"] = headers
                super().__init__(*args, **kwargs)
        return UploadClient

    openai.OpenAI = adapted(openai.OpenAI)
    openai.AsyncOpenAI = adapted(openai.AsyncOpenAI)


def child_command(command: list[str]) -> list[str]:
    if (len(command) > 1 and Path(command[0]).resolve() == Path(sys.executable).resolve()
            and command[1].endswith(".py")
            and Path(command[1]).resolve() != Path(__file__).resolve()):
        return [command[0], str(Path(__file__).resolve()), *command[1:]]
    return command


def install_pipeline_paths(module) -> None:
    """Adapt only the delivered knowledge config, before it is persisted."""
    original = module.build_kp_config

    @wraps(original)
    def build_kp_config(*args, **kwargs):
        config = original(*args, **kwargs)
        paths = dict(config.get("paths") or {})
        for key in ("input_chunks_glob", "outputs_dir", "prompts_dir", "final_output_dir"):
            value = paths.get(key)
            if isinstance(value, str):
                paths[key] = value.replace("\\", "/")
        return {**config, "paths": paths}

    module.build_kp_config = build_kp_config


def main() -> None:
    target = Path(sys.argv[1]).resolve()
    session = os.environ.setdefault("COMPETITION_UPLOAD_SESSION", new_upload_session())
    install_provider_clients(session)
    sys.path.insert(0, str(target.parent))
    # Only import the hook alongside the entry script, not arbitrary host modules.
    if (target.parent / "run_pdf_pipeline.py").is_file():
        import run_pdf_pipeline
        if hasattr(run_pdf_pipeline, "build_kp_config"):
            install_pipeline_paths(run_pdf_pipeline)
        original = run_pdf_pipeline.run_cmd

        def run_cmd(command, *args, **kwargs):
            return original(child_command(command), *args, **kwargs)

        run_pdf_pipeline.run_cmd = run_cmd
    sys.argv = [str(target), *sys.argv[2:]]
    if target.name == 'ingest_content.py' and os.environ.get('COMPETITION_PERSONAL_MANAGEMENT') == '1':
        import importlib.util
        from competition_app.services.personal_knowledge_storage import reserve_personal_kp_ids
        spec = importlib.util.spec_from_file_location('_personal_ingest_entry', target)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module.reserve_kp_ids = lambda stage, delivery: reserve_personal_kp_ids(stage, delivery, module.ORDER_FIELDS)
        module.main()
        return
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()