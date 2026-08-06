from competition_app.llm.openai_compatible import OpenAICompatibleChatModel, _format_user_data


def test_model_prompt_separates_dialogue_external_information_and_profile() -> None:
    rendered = _format_user_data({
        "user_request": "请讲解感冒",
        "shared_context": {
            "recent_conversation": [
                {"role": "user", "content": "你好"},
                {"role": "assistant", "content": "你好，请问想学习什么？"},
            ],
            "compressed_conversation": "user：此前学习过中医学基础。",
            "external_information": ["教材切片：感冒的病因病机"],
            "user_profile": {"learning_background": "零基础"},
        },
    })

    assert "【近期历史对话】\nuser：你好\nassistant：你好，请问想学习什么？" in rendered
    assert "【压缩历史对话】\nuser：此前学习过中医学基础。" in rendered
    assert "【外部信息】" in rendered
    assert "教材切片：感冒的病因病机" in rendered
    assert "【用户画像】" in rendered
    assert rendered.index("【用户画像】") < rendered.index("【压缩历史对话】")
    assert rendered.index("【压缩历史对话】") < rendered.index("【近期历史对话】")
    assert rendered.index("【近期历史对话】") < rendered.index("【外部信息】")
    history_section = rendered.split("【近期历史对话】", 1)[1].split(
        "【外部信息】", 1
    )[0]
    assert "教材切片" not in history_section


def test_user_profile_renders_as_natural_language_not_key_value_tree() -> None:
    rendered = _format_user_data({
        "shared_context": {
            "user_profile": {
                "basic_profile": {
                    "display_name": "王同学",
                    "gender": "male",
                    "region": "广东",
                    "onboarding_status": "completed",
                    "professional_background": "中医学大一在读",
                    "learning_goal": "通过执业医师资格考试",
                },
                "learning_state": {
                    "current_phase": "阶段一",
                    "mastery_avg": 0.62,
                    "recent_kps": [
                        {"kp_id": "K1", "name": "藏象", "mastery": 0.7},
                        {"kp_id": "K2", "name": "阴阳", "mastery": 0.65},
                    ],
                },
                "learning_monitoring": {
                    "current_status": "on_track",
                    "behavior_summary": "近7天完成4次任务",
                    "window_days": 7,
                },
                "current_plans": {
                    "long_term": {
                        "exists": True,
                        "status": "active",
                        "title": "30天备考计划",
                        "version": "v3",
                    },
                    "short_term": {"exists": False},
                },
                "snapshot": {"profile_updated_at": "2026-08-01T10:00:00Z"},
            },
            "compressed_conversation": "",
            "recent_conversation": [],
            "external_information": [],
        }
    })
    profile_section = rendered.split("【用户画像】", 1)[1].split(
        "【压缩历史对话】", 1
    )[0]

    assert "基础画像：姓名为王同学" in profile_section
    assert "性别为男" in profile_section
    assert "注册状态为已完成" in profile_section
    assert "平均掌握度为62%" in profile_section
    assert "藏象（掌握度为70%）" in profile_section
    assert "当前学习状态为总体在轨" in profile_section
    assert "状态为进行中，名称为30天备考计划" in profile_section
    assert "暂无短期计划" in profile_section
    assert "画像更新于2026-08-01" in profile_section
    # 结构化键名和元数据噪音键不应泄漏进提示词
    assert "basic_profile：" not in profile_section
    assert "display_name" not in profile_section
    assert "window_days" not in profile_section
    assert "exists" not in profile_section
    assert "version" not in profile_section


def test_empty_user_profile_keeps_no_assumption_fallback() -> None:
    rendered = _format_user_data({
        "shared_context": {
            "user_profile": {},
            "compressed_conversation": "",
            "recent_conversation": [],
            "external_information": [],
        }
    })
    assert "暂无已确认画像；不得自行推测。" in rendered


def test_user_profile_translates_real_english_fields_and_values() -> None:
    """真实生产画像结构：英文键与值必须全部中文化，噪音键不泄漏。"""
    rendered = _format_user_data({
        "shared_context": {
            "user_profile": {
                "basic_profile": {
                    "user_id": "USER_x",
                    "learner_group": "未选择用户群体",
                    "learning_goal": "中医执业医师资格考试",
                    "learning_background": "学过中医基础理论",
                    "daily_available_minutes": 30,
                    "l0_baseline": {
                        "stage_id": "L0",
                        "preferred_time_slot": "未填写",
                        "default_daily_tasks": 2,
                    },
                },
                "learning_profile": {
                    "case_reasoning_level": "emerging",
                    "question_accuracy": 0.0,
                    "sample_counts": {
                        "question_attempts": 0,
                        "mastery_records": 0,
                        "active_mistakes": 0,
                    },
                    "current_status": {
                        "status_code": "T0",
                        "status_name": "稳定学习",
                        "confidence": 0.86,
                        "evidence": ["当前处于稳定学习"],
                    },
                    "behavior_metrics": {
                        "task_completion_rate": 0.0,
                        "login_weekly_change": 1.0,
                        "metric_availability": {
                            "task_completion_rate": True,
                            "focus_time_change": False,
                        },
                        "evidence_status": "sufficient",
                        "data_source": "canonical_learning_monitoring",
                    },
                },
                "learning_state": {
                    "has_long_term_plan": True,
                    "due_review_count": 0,
                    "data_quality": {
                        "window_start": "2026-07-07T06:17:28.712764+00:00",
                        "window_end": "2026-08-06T06:17:28.712764+00:00",
                        "coverage": 0.1429,
                        "allow_cautious_path_adjustment": False,
                        "limitations": ["缺失指标保持不可用，不参与正向评分。",
                                        "掌握度是当前学习状态估计。"],
                    },
                    "hard_constraint_summary": [
                        {"key": "approved_route_available", "passed": True,
                         "reason": "approved_route_present"},
                        {"key": "low_data_protection", "passed": False,
                         "reason": "insufficient_data_for_high_risk_adjustment"},
                    ],
                },
                "learning_monitoring": {
                    "evidence_status": "sufficient",
                    "freshness_status": "fresh",
                    "window_days": 7,
                },
            },
            "compressed_conversation": "",
            "recent_conversation": [],
            "external_information": [],
        }
    })
    section = rendered.split("【用户画像】", 1)[1].split(
        "【压缩历史对话】", 1
    )[0]

    # 键名翻译
    assert "用户群体为未选择用户群体" in section
    assert "学习背景为学过中医基础理论" in section
    assert "每日可用时长为30" in section
    assert "案例推理水平为起步" in section
    assert "答题正确率为0%" in section
    assert "状态代码为T0，状态名称为稳定学习" in section
    assert "任务完成率为0%，周登录变化为100%" in section
    assert "数据来源为标准学习监控" in section
    # 保留系统既有标签（供 _fact_lines 共用），值仍翻译为是/否
    assert "当前是否存在长期规划（有当前有效版本才为True）为是" in section
    # 布尔 / 比率 / 时间戳
    assert "允许谨慎调整路径为否" in section
    assert "覆盖率为14%" in section
    assert "窗口开始为2026-07-07" in section
    assert "窗口结束为2026-08-06" in section
    # 值翻译与长列表分隔（任一项超过 20 字时用分号）
    assert "证据状态为充分" in section
    assert "硬约束摘要：约束项为已批准路线可用、是否通过为是" in section
    assert "原因为高风险调整数据不足" in section
    assert "限制说明为缺失指标保持不可用，不参与正向评分。、掌握度是当前学习状态估计。" in section
    # 噪音键 / 未填写 / 无信息字段不泄漏
    assert "user_id" not in section
    assert "metric_availability" not in section
    assert "confidence" not in section
    assert "window_days" not in section
    assert "未填写" not in section
    assert "T0" in section  # 状态代码本身保留


def test_provider_prompt_uses_only_the_fixed_four_context_headings() -> None:
    client = OpenAICompatibleChatModel(
        base_url="https://example.test/v1",
        api_key="secret",
        model="test-model",
    )
    messages = client._build_messages(
        "expert_agent",
        {
            "payload": {
                "shared_context": {
                    "user_profile": {"学习背景": "零基础"},
                    "compressed_conversation": "user：此前学习过基础理论。",
                    "recent_conversation": [
                        {"role": "user", "content": "请详细讲解当前知识点"}
                    ],
                    "external_information": [],
                },
                "topic": "阴阳学说",
            }
        },
        strict_json=False,
    )
    prompt = messages[1]["content"]
    headings = [
        "【用户画像】",
        "【压缩历史对话】",
        "【近期历史对话】",
        "【外部信息】",
    ]
    assert all(prompt.count(item) == 1 for item in headings)
    assert [prompt.index(item) for item in headings] == sorted(
        prompt.index(item) for item in headings
    )
    assert "请详细讲解当前知识点" in prompt
