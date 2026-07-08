import json
import subprocess

from src import bg_jobs


def test_load_ignores_non_object_store(tmp_path, monkeypatch):
    store = tmp_path / "bg_jobs.json"
    store.write_text(json.dumps(["not", "a", "job", "store"]), encoding="utf-8")
    monkeypatch.setattr(bg_jobs, "_STORE", store)

    assert bg_jobs._load() == {}


def test_load_keeps_only_object_job_records(tmp_path, monkeypatch):
    store = tmp_path / "bg_jobs.json"
    store.write_text(
        json.dumps(
            {
                "good": {"id": "good", "status": "done"},
                "bad-list": ["not", "a", "job"],
                "bad-null": None,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(bg_jobs, "_STORE", store)

    assert bg_jobs._load() == {"good": {"id": "good", "status": "done"}}


def test_launch_persists_record_before_popen_failure(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "bg_jobs"
    jobs_dir.mkdir()
    monkeypatch.setattr(bg_jobs, "_STORE", tmp_path / "bg_jobs.json")
    monkeypatch.setattr(bg_jobs, "_JOBS_DIR", jobs_dir)
    monkeypatch.setattr(bg_jobs, "find_bash", lambda: None)

    def fail_popen(*args, **kwargs):
        raise OSError("boom")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    rec = bg_jobs.launch("echo hi", "session-a")
    stored = bg_jobs._load()

    assert rec["status"] == "failed"
    assert rec["launch_error"] == "boom"
    assert rec["id"] in stored
    assert stored[rec["id"]]["status"] == "failed"
