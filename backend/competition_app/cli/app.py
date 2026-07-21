from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from competition_app.application.container import ApplicationContainer
from competition_app.application.personalized_review_card import ReviewCardRequest
from competition_app.config import Settings
from competition_app.db.bootstrap import DatabaseBootstrap


app = typer.Typer(no_args_is_help=True)


@app.command("init-db")
def init_db() -> None:
    settings = Settings.from_env()
    engine = DatabaseBootstrap(settings).ensure_database()
    engine.dispose()
    typer.echo("database initialized")


@app.command("run-review-card")
def run_review_card(
    learner_id: str = typer.Option(...),
    user_request: str = typer.Option(..., help="用户本次自然语言诉求，由 Planner 动态编排"),
    available_minutes: int = typer.Option(15, min=1),
    snapshot_root: Path | None = typer.Option(None),
    stream: bool = typer.Option(False, "--stream", help="在终端显示各智能体的模型增量输出"),
    trace_level: Literal["summary", "model", "full"] = typer.Option(
        "model", "--trace-level", help="流式追踪详情等级"
    ),
) -> None:
    import asyncio

    settings = Settings.from_env()
    container = ApplicationContainer.build(
        settings,
        snapshot_root=snapshot_root,
        stream_model_output=stream,
        trace_level=trace_level,
        include_backend_handoff=False,
    )
    result = asyncio.run(
        container.review_card_use_case.execute(
            ReviewCardRequest(
                learner_id=learner_id,
                user_request=user_request,
                available_minutes=available_minutes,
            )
        )
    )
    if stream:
        kp_id = (
            result.review_task.primary_kp_id
            if result.review_task is not None
            else "n/a"
        )
        audit_decision = result.audit.decision if result.audit is not None else "n/a"
        title = result.resource.title if result.resource is not None else "n/a"
        typer.echo(
            "\n[final] "
            f"status={result.status} "
            f"kp_id={kp_id} "
            f"audit={audit_decision} "
            f"title={title}\n"
            f"[snapshot] {result.snapshot_path}"
        )
    else:
        typer.echo(result.model_dump_json())


@app.command("serve")
def serve(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "competition_app.main:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        factory=False,
    )


if __name__ == "__main__":
    app()
