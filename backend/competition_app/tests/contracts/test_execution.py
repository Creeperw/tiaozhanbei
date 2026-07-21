import pytest

from competition_app.contracts.execution import ExecutionPlan, ExecutionStep, PlanValidationError


def test_all_contract_modules_are_importable() -> None:
    from competition_app.contracts import base, execution, knowledge, memory, resource, review

    assert all((base, execution, knowledge, memory, resource, review))


def test_valid_execution_dag_returns_topological_levels() -> None:
    plan = ExecutionPlan(
        plan_id="PLAN_001",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="memory", agent="memory_agent"),
            ExecutionStep(step_id="knowledge", agent="knowledge_base_agent"),
            ExecutionStep(
                step_id="diagnosis",
                agent="diagnosis_agent",
                depends_on=["memory", "knowledge"],
            ),
        ],
    )

    assert plan.topological_levels() == [["memory", "knowledge"], ["diagnosis"]]


def test_execution_plan_rejects_unknown_dependency() -> None:
    plan = ExecutionPlan(
        plan_id="PLAN_002",
        task_type="personalized_review_card",
        steps=[ExecutionStep(step_id="diagnosis", agent="diagnosis_agent", depends_on=["missing"])],
    )

    with pytest.raises(PlanValidationError, match="unknown dependency"):
        plan.validate_dag()


def test_execution_plan_rejects_cycles() -> None:
    plan = ExecutionPlan(
        plan_id="PLAN_003",
        task_type="personalized_review_card",
        steps=[
            ExecutionStep(step_id="memory", agent="memory_agent", depends_on=["diagnosis"]),
            ExecutionStep(step_id="diagnosis", agent="diagnosis_agent", depends_on=["memory"]),
        ],
    )

    with pytest.raises(PlanValidationError, match="cycle"):
        plan.validate_dag()
