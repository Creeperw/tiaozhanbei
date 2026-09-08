import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from competition_app.application.personalized_review_card import (
    PersonalizedReviewCardUseCase, ReviewCardRequest, WorkflowResumeRequest,
)
from competition_app.llm.provider_session import (
    bind_provider_session, current_provider_session, reset_provider_session,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_execute_resume_bind_same_session_and_restore_on_failure(error_type):
    usecase = object.__new__(PersonalizedReviewCardUseCase)
    usecase.model_trace_recorder = None
    usecase._remember_run = Mock()
    usecase._record_run_failure = Mock()
    usecase.get_run_state = Mock(return_value={})
    seen = []

    async def fail(*args, **kwargs):
        seen.append(current_provider_session())
        raise error_type("test failure")

    usecase._execute_started_run = AsyncMock(side_effect=fail)
    usecase._resume_started_run = AsyncMock(side_effect=fail)
    request = ReviewCardRequest.model_construct(
        learner_id="private-user", thread_id="THREAD_session_test",
        conversation_id=None, user_request="test", system_operation=False,
        operation_id=None,
    )
    resume = WorkflowResumeRequest.model_construct(answer="test")
    token = bind_provider_session("outer-scope")
    outer = current_provider_session()
    try:
        with pytest.raises(error_type):
            await usecase.execute(request)
        assert current_provider_session() == outer
        with pytest.raises(error_type):
            await usecase.resume("THREAD_session_test", resume)
        assert current_provider_session() == outer
        assert seen[0] == seen[1]
        assert seen[0] != outer
    finally:
        reset_provider_session(token)