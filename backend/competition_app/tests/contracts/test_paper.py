from competition_app.contracts.paper import BlueprintUnit


def test_blueprint_unit_does_not_require_difficulty() -> None:
    unit = BlueprintUnit(
        unit_id="UNIT_01",
        sequence=1,
        knowledge_module="四君子汤组成",
        learning_objective="识别组成药物",
        retrieval_query="四君子汤 组成",
        required_question_count=2,
    )

    assert unit.difficulty_preference is None
    assert unit.question_type_preferences == []