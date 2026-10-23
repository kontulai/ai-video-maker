"""One background job per project. The job's log lines are the ones the CLI prints
(through make_video.LOG_SINK) and are streamed to the browser as they come."""
from __future__ import annotations

import queue
import threading
from datetime import datetime
from typing import Callable, Iterator, Literal

from pydantic import BaseModel, Field

import make_video as mv
from . import projects

IDLE_TIMEOUT = 30.0   # seconds a subscriber waits for a line before giving up
_END = None


class JobBusy(Exception):
    """A job is already running for this project."""


class Job(BaseModel):
    phase: str
    status: Literal["running", "done", "failed"] = "running"
    log: list[str] = Field(default_factory=list)
    error: str = ""
    started: str
    finished: str | None = None
    scenes: list[int] | None = None   # video phase: the scenes being made, None for all


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class JobRunner:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._running: dict[str, Job] = {}
        self._queues: dict[str, list[queue.Queue]] = {}

    # -- starting -----------------------------------------------------------------
    def start(self, project_id: str, phase: str,
              fn: Callable[[Callable[[str], None]], None], scenes: list[int] | None = None) -> Job:
        with self._lock:
            if project_id in self._running:
                raise JobBusy(project_id)
            job = Job(phase=phase, started=_now(), scenes=scenes)
            self._running[project_id] = job
            self._queues.setdefault(project_id, [])
        self._save(project_id, job)
        thread = threading.Thread(target=self._run, args=(project_id, job, fn), daemon=True)
        thread.start()
        return job

    def _run(self, project_id: str, job: Job, fn) -> None:
        def sink(line: str) -> None:
            with self._lock:
                job.log.append(line)
                listeners = list(self._queues.get(project_id, ()))
            for q in listeners:
                q.put(line)
            self._save(project_id, job)

        token = mv.LOG_SINK.set(sink)
        try:
            fn(mv.log)
            job.status = "done"
        except SystemExit as e:
            job.status, job.error = "failed", str(e.code if e.code is not None else "")
        except Exception as e:   # noqa: BLE001 - anything the pipeline raises ends the job
            job.status, job.error = "failed", str(e) or type(e).__name__
        finally:
            mv.LOG_SINK.reset(token)
            job.finished = _now()
            with self._lock:
                self._running.pop(project_id, None)
                listeners = self._queues.pop(project_id, [])
            self._save(project_id, job)
            for q in listeners:
                q.put(_END)

    # -- reading --------------------------------------------------------------------
    def current(self, project_id: str) -> Job | None:
        with self._lock:
            job = self._running.get(project_id)
        if job is not None:
            return job
        try:
            path = projects.folder(project_id) / "job.json"
        except Exception:
            return None
        if not path.exists():
            return None
        try:
            return Job.model_validate_json(path.read_text())
        except ValueError:
            return None

    def subscribe(self, project_id: str) -> Iterator[str]:
        """The lines so far, then new ones until the job ends."""
        with self._lock:
            job = self._running.get(project_id)
            if job is None:
                saved = None
            else:
                q: queue.Queue = queue.Queue()
                self._queues.setdefault(project_id, []).append(q)
                snapshot = list(job.log)
        if job is None:
            saved = self.current(project_id)
            yield from (saved.log if saved else [])
            return
        yield from snapshot
        while True:
            try:
                line = q.get(timeout=IDLE_TIMEOUT)
            except queue.Empty:
                with self._lock:
                    still = self._running.get(project_id) is job
                if not still:
                    return
                continue
            if line is _END:
                return
            yield line

    def _save(self, project_id: str, job: Job) -> None:
        try:
            folder = projects.folder(project_id)
            if folder.exists():
                (folder / "job.json").write_text(job.model_dump_json(indent=2))
        except OSError:
            pass
