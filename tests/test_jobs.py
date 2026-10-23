"""Background jobs: one per project, log lines captured from make_video.log and streamed."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import make_video as mv
from server import projects
from server.jobs import JobBusy, JobRunner


@pytest.fixture
def pid(tmp_path, monkeypatch):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    return projects.create("x").id


def wait_for(job, timeout=5.0):
    t0 = time.time()
    while job.status == "running" and time.time() - t0 < timeout:
        time.sleep(0.01)
    assert job.status != "running", "job did not finish"


def plain(line):
    return line.split("] ", 1)[-1]


def test_job_logs_and_finishes(pid):
    runner = JobRunner()

    def fn(log):
        mv.log("step one")
        log("step two")

    job = runner.start(pid, "voice", fn)
    assert job.phase == "voice" and job.status == "running"
    wait_for(job)
    assert job.status == "done" and job.error == "" and job.finished is not None
    assert [plain(line) for line in job.log] == ["step one", "step two"]
    saved = runner.current(pid)
    assert saved is not None and saved.status == "done" and saved.log == job.log
    # a fresh runner still finds the saved record
    assert JobRunner().current(pid).status == "done"
    assert runner.current("nope") is None


def test_job_failure_from_sys_exit(pid):
    runner = JobRunner()
    job = runner.start(pid, "plan", lambda log: sys.exit("boom"))
    wait_for(job)
    assert (job.status, job.error) == ("failed", "boom")

    def raiser(log):
        raise RuntimeError("bad thing")

    job = runner.start(pid, "plan", raiser)
    wait_for(job)
    assert (job.status, job.error) == ("failed", "bad thing")


def test_second_job_is_refused(pid):
    runner = JobRunner()
    gate = threading.Event()
    job = runner.start(pid, "video", lambda log: gate.wait(5))
    with pytest.raises(JobBusy):
        runner.start(pid, "voice", lambda log: None)
    gate.set()
    wait_for(job)
    # once done, a new job may start
    job2 = runner.start(pid, "voice", lambda log: None)
    wait_for(job2)
    assert job2.status == "done"


def test_subscribe_replays_then_follows(pid):
    runner = JobRunner()
    gate = threading.Event()

    def fn(log):
        log("first")
        gate.wait(5)
        log("second")

    job = runner.start(pid, "video", fn)
    t0 = time.time()
    while not job.log and time.time() - t0 < 5:
        time.sleep(0.01)
    it = runner.subscribe(pid)
    assert plain(next(it)) == "first"
    gate.set()
    rest = [plain(x) for x in it]
    assert rest == ["second"]
    wait_for(job)
    # after the job finished, subscribing replays the lines and stops
    assert [plain(x) for x in runner.subscribe(pid)] == ["first", "second"]
    assert list(runner.subscribe("nope")) == []
