from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts.start_openviking_glm47 import _start_logged_process, _wait_for_http


class FakeProcess:
    def __init__(self, poll_result=None):
        self.pid = 12345
        self._poll_result = poll_result

    def poll(self):
        return self._poll_result


def test_start_logged_process_detaches_process_group_and_keeps_log_handle(monkeypatch, tmp_path):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    managed = _start_logged_process(
        "embedding",
        ["python", "-m", "uvicorn"],
        cwd=tmp_path,
        env={"EXAMPLE": "1"},
        log_path=tmp_path / "embedding.log",
    )

    assert captured["cmd"] == ["python", "-m", "uvicorn"]
    assert captured["kwargs"]["cwd"] == tmp_path
    assert captured["kwargs"]["env"] == {"EXAMPLE": "1"}
    assert captured["kwargs"]["stderr"] == subprocess.STDOUT
    assert captured["kwargs"]["start_new_session"] is True
    assert managed.name == "embedding"
    assert managed.log_path == tmp_path / "embedding.log"
    assert managed.log_handle.closed is False

    managed.close_log()

    assert managed.log_handle.closed is True


def test_wait_for_http_fails_fast_when_managed_process_exits(monkeypatch, tmp_path):
    def fake_urlopen(*args, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("scripts.start_openviking_glm47.urllib.request.urlopen", fake_urlopen)
    process = FakeProcess(poll_result=7)

    with pytest.raises(RuntimeError, match="openviking exited before ready"):
        _wait_for_http(
            "openviking",
            "http://127.0.0.1:1933/ready",
            timeout=10,
            process=process,
            log_path=tmp_path / "openviking.log",
        )
