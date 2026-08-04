import asyncio

import pytest

from competition_app.application.container import StreamingChatModel


class DelayedModel:
    def __init__(self) -> None:
        self.started = 0
        self.both_started = asyncio.Event()

    async def complete_json(self, role, payload, on_delta=None):
        self.started += 1
        if self.started == 2:
            self.both_started.set()
        on_delta(f"{role}:start")
        await asyncio.wait_for(self.both_started.wait(), timeout=0.2)
        on_delta(f"{role}:end")
        return {"role": role}


@pytest.mark.asyncio
async def test_streaming_wrapper_does_not_serialize_parallel_model_calls(capsys) -> None:
    inner = DelayedModel()
    model = StreamingChatModel(inner)

    await asyncio.gather(
        model.complete_json("memory_agent", {}),
        model.complete_json("knowledge_base_agent", {}),
    )

    assert inner.started == 2
    output = capsys.readouterr().out
    assert output.index(">>> memory_agent") < output.index("<<< memory_agent")
    assert output.index(">>> knowledge_base_agent") < output.index(
        "<<< knowledge_base_agent"
    )
