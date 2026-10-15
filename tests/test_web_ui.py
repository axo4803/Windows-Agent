"""Verify the real browser transport against an inert desktop-agent substitute."""
from contextlib import nullcontext
import http.client
import json
import threading
import time

import pytest

from windows_agent.web.session import Session, validate_options
from windows_agent.web.server import LocalServer


def wait_for(session, predicate):
    end = time.monotonic() + 3
    while time.monotonic() < end:
        state = session.snapshot()
        if predicate(state):
            return state
        time.sleep(0.01)
    raise AssertionError(session.snapshot())


def fake_agent(args, manager, job):
    class Agent:
        stop = threading.Event()

        def check(self):
            if self.stop.is_set():
                raise KeyboardInterrupt()

        def run(self, task):
            manager.agent_event(job, "run_started", {})
            if task == "approval":
                try:
                    manager.await_approval(job, "Type into the selected application", self.check)
                except PermissionError:
                    manager.answer(job, "The action was denied.")
                    manager.agent_event(job, "run_completed", {"tool_calls": 0})
                    return
            elif task == "empty":
                return
            elif task == "error":
                manager.agent_event(job, "ollama_error", {"error": "offline"})
                return
            manager.agent_event(job, "tool_result", {"tool": "inspect_wifi", "result": {"ssid": "Example", "verified": True}, "call": 1})
            manager.answer(job, "Your SSID is Example.")
            manager.agent_event(job, "run_completed", {"tool_calls": 1})

    return Agent()


@pytest.fixture
def session():
    value = Session(agent_factory=fake_agent, thread_context=nullcontext)
    yield value
    value.close()


def test_success_preserves_answer_and_tool_evidence(session):
    identifier = session.start({"task": "wifi", "read_only": True})
    state = wait_for(session, lambda s: s["jobs"][-1]["status"] == "completed")
    assert state["jobs"][-1]["answer"] == "Your SSID is Example."
    assert state["jobs"][-1]["options"]["read_only"] is True
    assert any(e.get("details", {}).get("ssid") == "Example" for e in state["events"])
    assert session.snapshot(state["cursor"], identifier)["events"] == []
    assert session.snapshot(state["cursor"], "other-task")["events"]


@pytest.mark.parametrize("approved, expected", [(True, "Your SSID is Example."), (False, "The action was denied.")])
def test_approval_is_bound_to_task_and_nonce(session, approved, expected):
    identifier = session.start({"task": "approval"})
    state = wait_for(session, lambda s: s["approval"] is not None)
    with pytest.raises(RuntimeError):
        session.start({"task": "wifi"})
    with pytest.raises(ValueError):
        session.decide(identifier, "stale", True)
    session.decide(identifier, state["approval"]["id"], approved)
    state = wait_for(session, lambda s: s["jobs"][-1]["status"] == "completed")
    assert state["jobs"][-1]["answer"] == expected
    with pytest.raises(ValueError):
        session.decide(identifier, "stale", True)


def test_stop_releases_pending_approval_without_executing_action(session):
    identifier = session.start({"task": "approval"})
    state = wait_for(session, lambda s: s["approval"] is not None)
    session.stop(identifier)
    stopped = wait_for(session, lambda s: s["jobs"][-1]["status"] == "stopped")
    assert stopped["approval"] is None
    assert stopped["jobs"][-1]["answer"] == ""
    assert not any(e["kind"] == "tool_result" for e in stopped["events"])
    with pytest.raises(ValueError):
        session.decide(identifier, state["approval"]["id"], True)


def test_stop_before_agent_initialization_is_respected():
    gate = threading.Event()
    def factory(args, manager, job):
        assert gate.wait(2)
        return fake_agent(args, manager, job)
    session = Session(agent_factory=factory, thread_context=nullcontext)
    try:
        identifier = session.start({"task": "wifi"})
        session.stop(identifier)
        gate.set()
        state = wait_for(session, lambda s: s["jobs"][-1]["status"] == "stopped")
        assert not state["jobs"][-1]["answer"]
    finally:
        gate.set()
        session.close()


@pytest.mark.parametrize("task, status", [("empty", "stopped"), ("error", "error")])
def test_no_success_claim_without_completed_event(session, task, status):
    session.start({"task": task})
    state = wait_for(session, lambda s: s["jobs"][-1]["status"] == status)
    assert not state["jobs"][-1]["answer"]


@pytest.mark.parametrize("payload", [{"task": " "}, {"task": "x" * 12001}, {"task": "ok", "read_only": "true"}, {"task": "ok", "max_seconds": 3601}, {"task": "ok", "max_tools": True}, {"task": "ok", "model": ""}])
def test_invalid_tasks_rejected(payload):
    with pytest.raises(ValueError):
        validate_options(payload)


@pytest.fixture
def server(session):
    value = LocalServer(0, session=session)
    thread = threading.Thread(target=value.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield value
    value.shutdown()
    value.server_close()
    thread.join(timeout=2)


def request(server, path, body=None, headers=None):
    connection = http.client.HTTPConnection(*server.server_address, timeout=3)
    defaults = {"X-Agent-Token": server.token}
    if body is not None:
        defaults["Content-Type"] = "application/json"
    defaults.update(headers or {})
    connection.request("POST" if body is not None else "GET", path, body=json.dumps(body) if body is not None else None, headers=defaults)
    response = connection.getresponse()
    data = response.read()
    connection.close()
    return response.status, data


def test_http_assets_and_task_lifecycle(server):
    for path in ("/", "/style.css", "/app.js"):
        status, body = request(server, path)
        assert status == 200 and body
    status, body = request(server, "/api/tasks", {"task": "wifi"})
    assert status == 200 and json.loads(body)["id"]
    wait_for(server.session, lambda s: s["jobs"][-1]["status"] == "completed")
    status, body = request(server, "/api/state")
    assert status == 200 and json.loads(body)["jobs"][-1]["answer"]
    assert request(server, "/../desktop_agent.py")[0] == 404


def test_http_rejects_other_origins_hosts_and_missing_token(server):
    assert request(server, "/api/tasks", {"task": "wifi"}, {"Origin": "https://untrusted.example"})[0] == 403
    assert request(server, "/api/bootstrap", headers={"Host": "untrusted.example"})[0] == 403
    assert request(server, "/api/tasks", {"task": "wifi"}, {"X-Agent-Token": ""})[0] == 403
    assert request(server, "/api/state", headers={"X-Agent-Token": ""})[0] == 403
    assert not server.session.snapshot()["jobs"]


def test_desktop_size_endpoint_is_scoped_and_validates_input(server):
    from unittest.mock import Mock
    assert request(server, '/api/desktop-window', {'width':1200,'height':800})[0] == 404
    server.desktop_size_callback = Mock()
    assert request(server, '/api/desktop-window', {'width':True,'height':800})[0] == 400
    assert request(server, '/api/desktop-window', {'width':1200,'height':800})[0] == 200
    server.desktop_size_callback.assert_called_once_with(1200,800)


def test_http_approval_and_stop_controls(server):
    status, body = request(server, "/api/tasks", {"task": "approval"})
    assert status == 200
    identifier = json.loads(body)["id"]
    state = wait_for(server.session, lambda s: s["approval"] is not None)
    assert request(server, "/api/approval", {"job": identifier, "approval": "stale", "approved": True})[0] == 400
    assert request(server, "/api/approval", {"job": identifier, "approval": state["approval"]["id"], "approved": True})[0] == 200
    wait_for(server.session, lambda s: s["jobs"][-1]["status"] == "completed")
    _, body = request(server, "/api/tasks", {"task": "approval"})
    identifier = json.loads(body)["id"]
    wait_for(server.session, lambda s: s["approval"] is not None)
    assert request(server, "/api/stop", {"job": identifier})[0] == 200
    state = wait_for(server.session, lambda s: s["jobs"][-1]["status"] == "stopped")
    assert state["approval"] is None and not state["jobs"][-1]["answer"]


def test_new_chat_clears_conversation_but_keeps_preferences(session, monkeypatch, tmp_path):
    from windows_agent import context
    history = tmp_path / "conversation.json"
    preferences = tmp_path / "preferences.json"
    monkeypatch.setattr(context, "HISTORY", history)
    history.write_text('{"exchanges": [{"user": "old"}]}', encoding="utf-8")
    preferences.write_text('{"theme": "green"}', encoding="utf-8")
    session.start({"task": "wifi"})
    wait_for(session, lambda s: s["jobs"][-1]["status"] == "completed")
    session.reset()
    assert json.loads(history.read_text(encoding="utf-8")) == {"exchanges": []}
    assert preferences.read_text(encoding="utf-8") == '{"theme": "green"}'
    assert not session.snapshot()["jobs"]
