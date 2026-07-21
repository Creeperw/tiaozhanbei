from __future__ import annotations

from collections.abc import Callable


def terminal_delta_printer(agent: str) -> Callable[[str], None]:
    started = False

    def print_delta(delta: str) -> None:
        nonlocal started
        if not started:
            print(f"\n[{agent}] 模型输出", flush=True)
            started = True
        print(delta, end="", flush=True)

    return print_delta


def terminal_agent_started(agent: str) -> None:
    print(f"\n>>> {agent}: 开始调用模型", flush=True)


def terminal_agent_finished(agent: str) -> None:
    print(f"\n<<< {agent}: 模型输出完成", flush=True)