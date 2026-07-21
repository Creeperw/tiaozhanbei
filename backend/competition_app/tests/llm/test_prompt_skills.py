from competition_app.llm.prompt_skills import PromptSkillRegistry, prompt_skill_registry


def test_prompt_skill_registry_loads_approved_task_skill() -> None:
    skill = prompt_skill_registry.load("diagnosis_agent", "learning_plan")

    assert skill.skill_id == "diagnosis.create_learning_plan"
    assert skill.version == "1.3.0"
    assert "长期计划六栏" in skill.instructions
    assert "long_term_plan_stages" in skill.instructions
    assert skill.as_model_input()["task_type"] == "learning_plan"


def test_review_card_skills_allow_labeled_consensus_extensions() -> None:
    expert = prompt_skill_registry.load("expert_agent", "personalized_review_card")
    audit = prompt_skill_registry.load("audit_agent", "personalized_review_card")

    assert "核心结论" in expert.instructions
    assert "补充说明" in expert.instructions
    assert "不同教材存在口径差异" in expert.instructions
    assert "非阻断建议" in audit.instructions
    assert "仍应 pass" in audit.instructions
    assert "明确事实错误" in audit.instructions


def test_prompt_skill_registry_rejects_unregistered_or_traversal_paths(tmp_path) -> None:
    registry = PromptSkillRegistry(tmp_path)

    for agent, task_type in [("diagnosis_agent", "missing"), ("..", "secret")]:
        try:
            registry.load(agent, task_type)
        except KeyError:
            pass
        else:
            raise AssertionError("unregistered prompt skill must be rejected")
