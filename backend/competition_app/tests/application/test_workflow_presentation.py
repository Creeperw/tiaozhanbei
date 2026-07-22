import json

from competition_app.application.workflow_presentation import workflow_result_to_markdown


def test_long_term_plan_message_includes_system_owned_stage_data() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "long_term",
            "long_term_plan": {
                "content": "【最终目标】建立中医基础。",
                "stages": [
                    {
                        "stage": 1,
                        "book": ["《中医学基础》"],
                        "goal": "建立基础理论框架。",
                    }
                ],
            },
        },
    })

    assert "【阶段路线数据】" in message
    encoded = message.split("```json\n", 1)[1].split("\n```", 1)[0]
    assert json.loads(encoded) == {
        "long_term_plan_stages": [
            {
                "stage": 1,
                "book": ["《中医学基础》"],
                "goal": "建立基础理论框架。",
            }
        ]
    }


def test_short_term_message_does_not_repeat_stale_long_term_stages() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "short_term",
            "long_term_plan": None,
            "short_term_plan": {"content": "【当前主目标】完成本周学习。"},
        },
    })

    assert "long_term_plan_stages" not in message
