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


def test_daily_task_message_names_chapter_and_focus_knowledge_points() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "learning_plan": {
            "generated_scope": "daily_task",
            "learning_task": {
                "task_content": "精读阴阳学说并整理笔记。",
                "learning_chapter": "《中医学基础》阴阳学说",
                "focus_knowledge_points": ["阴阳对立制约", "阴阳互根互用"],
                "estimated_minutes": 45,
                "completion_criteria": "能够闭卷解释两个概念。",
            },
        },
    })

    assert "今日章节：《中医学基础》阴阳学说" in message
    assert "重点知识点：阴阳对立制约、阴阳互根互用" in message
    assert "预计用时：45 分钟" in message


def test_paper_message_keeps_exam_body_in_workspace() -> None:
    message = workflow_result_to_markdown({
        "status": "success",
        "task_type": "paper_generation",
        "resource": {
            "title": "四君子汤试卷",
            "content": {"试卷正文": [{"题干": "不应出现在对话里"}]},
        },
        "ui_actions": [
            {
                "label": "开始答题",
                "destination": "workshop.paper",
                "params": {"paper_id": "PAPER_1"},
            }
        ],
    })

    assert "不应出现在对话里" not in message
    assert "开始答题" in message
    assert "通过审核" in message
