from pathlib import Path

from typer.testing import CliRunner

from competition_app.cli.app import app


def test_cli_runs_review_card_in_stub_mode(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-review-card",
            "--learner-id", "L1",
            "--user-request", "生成四君子汤复习卡",
            "--snapshot-root", str(tmp_path),
        ],
        env={"COMPETITION_APP_MODE": "stub"},
    )

    assert result.exit_code == 0
    assert '"status":"success"' in result.stdout.replace(" ", "")
    assert "四君子汤个性化复习卡" in result.stdout