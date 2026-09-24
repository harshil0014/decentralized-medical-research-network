import os
import tempfile
from contextlib import contextmanager
from threading import Event, Thread


with tempfile.TemporaryDirectory() as root:
    os.environ["MEDICAL_REGISTRY_RUNTIME_DIR"] = root
    from backend.runtime_security import require_job_lock

    entered = Event()
    release = Event()
    unrelated_done = Event()
    duplicate_done = Event()

    def first():
        with contextmanager(require_job_lock)("job-a"):
            entered.set()
            assert release.wait(5)

    def unrelated():
        assert entered.wait(5)
        with contextmanager(require_job_lock)("job-b"):
            unrelated_done.set()

    def duplicate():
        assert entered.wait(5)
        with contextmanager(require_job_lock)("job-a"):
            duplicate_done.set()

    threads = [Thread(target=first), Thread(target=unrelated), Thread(target=duplicate)]
    for thread in threads:
        thread.start()
    assert unrelated_done.wait(5), "Unrelated HE job was serialized"
    assert not duplicate_done.is_set(), "Duplicate HE job was not serialized"
    release.set()
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()
    assert duplicate_done.is_set()

print("PER-JOB CONCURRENCY LOCKS: PASS")
