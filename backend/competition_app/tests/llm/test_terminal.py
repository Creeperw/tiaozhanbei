import asyncio

import pytest

from competition_app.application.container import StreamingChatModel


class DelayedModel:
    async def complete_json(self, role, payload, on_delta=None):
        on_delta(f"{role}:start")
        await asyncio.sleep(0.01)
        on_delta(f"{role}:end")
        return {"role": role}


@pytest.mark.asyncio
async def test_streaming_wrapper_serializes_model_calls_for_readable_terminal(capsys) -> None:
    model = StreamingChatModel(DelayedModel())

    await asyncio.gather(
        model.complete_json("memory_agent", {}),
        model.complete_json("knowledge_base_agent", {}),
    )

    output = capsys.readouterr().out
    memory_start = output.index(">>> memory_agent")
    memory_end = output.index("<<< memory_agent")
    knowledge_start = output.index(">>> knowledge_base_agent")
    knowledge_end = output.index("<<< knowledge_base_agent")
    assert memory_end < knowledge_start or knowledge_end < memory_start