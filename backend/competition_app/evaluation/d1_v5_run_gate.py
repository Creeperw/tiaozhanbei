from __future__ import annotations

"""Process-local exclusive lease for the isolated D1 V5 evaluation workflow."""

from threading import RLock


class D1V5EvaluationRunGate:
    """Allow exactly one discovery/evolution workflow to own model capacity."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._owner: str | None = None

    @property
    def owner(self) -> str | None:
        with self._lock:
            return self._owner

    def acquire(self, owner: str) -> None:
        owner = str(owner or "").strip()
        if not owner:
            raise ValueError("evaluation lease owner is required")
        with self._lock:
            if self._owner not in {None, owner}:
                raise ValueError(
                    "another D1 V5 evaluation workflow is already active"
                )
            self._owner = owner

    def handoff(self, expected_owner: str, new_owner: str) -> None:
        expected_owner = str(expected_owner or "").strip()
        new_owner = str(new_owner or "").strip()
        if not expected_owner or not new_owner:
            raise ValueError("evaluation lease handoff owners are required")
        with self._lock:
            if self._owner is None:
                # Allows restoration or a test-created frozen discovery artifact.
                self._owner = new_owner
                return
            if self._owner != expected_owner:
                raise ValueError(
                    "another D1 V5 evaluation workflow is already active"
                )
            self._owner = new_owner

    def release(self, owner: str) -> bool:
        with self._lock:
            if self._owner != owner:
                return False
            self._owner = None
            return True
