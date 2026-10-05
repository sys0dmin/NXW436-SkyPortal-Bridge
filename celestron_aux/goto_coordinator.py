"""Asynchronous, fake-profile GoTo orchestration.

This module owns jobs and cancellation only; the existing MountController and
RelativePositionController remain the motion authority. Hardware GoTo is not
enabled by this coordinator unless an application explicitly injects it.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from mount_api import Axis, GotoResult, MountController


class GotoState(str, Enum):
    IDLE = "IDLE"
    GOTO_ACTIVE = "GOTO_ACTIVE"
    STOPPING = "STOPPING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass
class GotoJob:
    axis: Axis
    target: int
    variant: int
    state: GotoState = GotoState.IDLE
    started_at: float | None = None
    finished_at: float | None = None
    result: GotoResult | None = None
    error: str | None = None
    motion_stopped: bool = False
    failure_details: dict = field(default_factory=dict)
    cancellation: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


class GoToCoordinator:
    """Owns at most one asynchronous GoTo job per axis."""

    def __init__(self, controller: MountController, *, enabled: bool = False,
                 clock: Any = time.monotonic,
                 state_observer: Callable[[GotoJob], None] | None = None,
                 target_validator: Callable[[Axis, int], None] | None = None) -> None:
        self.controller = controller
        self.enabled = enabled
        self.clock = clock
        self._lock = threading.RLock()
        self._jobs: dict[Axis, GotoJob] = {}
        self.state_observer = state_observer
        self.target_validator = target_validator

    def job(self, axis: Axis) -> GotoJob | None:
        with self._lock:
            return self._jobs.get(axis)

    def state(self, axis: Axis) -> GotoState:
        with self._lock:
            job = self._jobs.get(axis)
            return GotoState.IDLE if job is None else job.state

    def is_active(self, axis: Axis) -> bool:
        return self.state(axis) in {GotoState.GOTO_ACTIVE, GotoState.STOPPING}

    def start(self, axis: Axis, target: int, *, variant: int) -> GotoJob:
        if not self.enabled:
            raise RuntimeError("asynchronous GoTo is disabled for this profile")
        if self.target_validator is not None:
            self.target_validator(axis, target)
        with self._lock:
            if self.is_active(axis):
                raise RuntimeError(f"GoTo already active for {axis.value}")
            job = GotoJob(axis=axis, target=target, variant=variant,
                          state=GotoState.GOTO_ACTIVE, started_at=self.clock())
            thread = threading.Thread(target=self._run, args=(job,),
                                      name=f"goto-{axis.value}", daemon=False)
            job.thread = thread
            self._jobs[axis] = job
            thread.start()
        self._notify(job)
        return job

    def cancel(self, axis: Axis, *, timeout: float = 2.0) -> bool:
        with self._lock:
            job = self._jobs.get(axis)
            if job is None or not self.is_active(axis):
                return True
            job.state = GotoState.STOPPING
            job.cancellation.set()
            thread = job.thread
        self._notify(job)
        if thread is not None:
            thread.join(timeout)
        return not self.is_active(axis)

    def _run(self, job: GotoJob) -> None:
        try:
            if job.axis is Axis.AZ:
                result = self.controller.goto_az(job.target, cancellation_event=job.cancellation)
            else:
                result = self.controller.goto_alt(job.target, cancellation_event=job.cancellation)
        except Exception as error:
            with self._lock:
                job.finished_at = self.clock()
                if job.cancellation.is_set() or "cancelled" in str(error):
                    job.state = GotoState.CANCELLED
                else:
                    job.state = GotoState.FAILED
                job.error = f"{type(error).__name__}: {error}"
                job.motion_stopped = bool(getattr(error, "motion_stopped", False))
                job.failure_details = dict(getattr(error, "details", {}))
            self._notify(job)
            return
        with self._lock:
            job.finished_at = self.clock()
            job.result = result
            job.state = GotoState.CANCELLED if job.cancellation.is_set() else GotoState.COMPLETED
        self._notify(job)

    def _notify(self, job: GotoJob) -> None:
        if self.state_observer is not None:
            self.state_observer(job)
