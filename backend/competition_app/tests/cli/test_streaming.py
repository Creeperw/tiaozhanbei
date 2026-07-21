from pathlib import Path

from typer.testing import CliRunner

from competition_app.cli.app import app


def test_cli_stream_option_prints_agent_model_output(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-review-card",
            "--learner-id", "stream_user",
            "--user-request", "生成四君子汤复习卡",
            "--snapshot-root", str(tmp_path),
            "--stream",
        ],
        env={"COMPETITION_APP_MODE": "stub"},
    )

    assert result.exit_code == 0
    assert "[planner_agent]" in result.stdout
    assert "[audit_agent]" in result.stdout
    assert "[final] status=success" in result.stdout
    assert '"agent_outputs"' not in result.stdout


def test_cli_summary_trace_hides_model_deltas(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-review-card",
            "--learner-id", "stream_summary_user",
            "--user-request", "生成四君子汤复习卡",
            "--snapshot-root", str(tmp_path),
            "--stream",
            "--trace-level", "summary",
        ],
        env={"COMPETITION_APP_MODE": "stub"},
    )

    assert result.exit_code == 0
    assert "开始调用模型" in result.stdout
    assert "协议校验" in result.stdout
    assert "模型输出\n{" not in result.stdout


def test_cli_stream_option_supports_paper_generation_without_review_task(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-review-card",
            "--learner-id", "stream_paper_user",
            "--user-request", "请生成2道选择题试卷",
            "--snapshot-root", str(tmp_path),
            "--stream",
        ],
        env={"COMPETITION_APP_MODE": "stub"},
    )

    assert result.exit_code == 0
    assert "[final] status=success" in result.stdout
    assert "title=" in result.stdout