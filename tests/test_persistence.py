from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
from ollama import ChatResponse

import agent_runtime
import desktop_agent


def reply(text):
    return ChatResponse(message={"role": "assistant", "content": text})


def tool(name, **arguments):
    return ChatResponse(message={"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]})


@pytest.fixture
def harness(monkeypatch, tmp_path):
    agent = desktop_agent.DesktopAgent.__new__(desktop_agent.DesktopAgent)
    agent.args = SimpleNamespace(model="test", read_only=True, online_docs=False,
                                 max_tools=12, max_turns=16, max_seconds=60)
    agent.calls = 0
    agent.stop = threading.Event()
    agent.log_path = tmp_path / "unused.jsonl"
    agent.log_event = Mock()
    agent.check = Mock()
    agent.inspect_storage = Mock(return_value={"model": "Example SSD", "verified": True})
    agent.read_system_profile = Mock(return_value={"storage": None})
    agent.inspect_system = Mock(return_value={"memory": "16 GB"})
    client = Mock()
    monkeypatch.setattr(desktop_agent, "Client", Mock(return_value=client))
    monkeypatch.setattr(desktop_agent.system_profile, "context", lambda task: "")
    monkeypatch.setattr(desktop_agent.agent_context, "history", lambda: [])
    record = Mock()
    monkeypatch.setattr(desktop_agent.agent_context, "record", record)
    show = Mock()
    monkeypatch.setattr(desktop_agent, "print_agent_response", show)
    return agent, client, record, show


def test_tentative_offer_recovers_to_live_answer_without_user_push(harness):
    agent, client, record, show = harness
    client.chat.side_effect = [tool("read_system_profile", section="storage"),
        reply("The profile is empty. Do you want me to inspect storage?"),
        tool("inspect_storage"), reply("Your SSD is Example SSD."),
        reply("Your SSD is Example SSD.")]
    agent.run("What SSD do I have?")
    agent.inspect_storage.assert_called_once()
    show.assert_called_once_with("Your SSD is Example SSD.")
    assert record.call_args.args[0] == "What SSD do I have?"
    assert client.chat.call_count == 5


def test_two_failures_block_same_action_but_allow_alternative(harness):
    agent, client, record, show = harness
    agent.read_system_profile.side_effect = [ValueError("first"), ValueError("different error")]
    client.chat.side_effect = [tool("read_system_profile", section="storage") for _ in range(3)] + [
        tool("inspect_storage"), reply("Example SSD"), reply("Example SSD")]
    agent.run("Identify my SSD")
    assert agent.read_system_profile.call_count == 2
    agent.inspect_storage.assert_called_once()
    assert record.call_args.args[2][2]["outcome"] == "error"
    show.assert_called_once_with("Example SSD")


def test_denied_action_is_not_retried_and_independent_work_continues(harness):
    agent, client, record, show = harness
    agent.inspect_storage.side_effect = PermissionError("Denied")
    client.chat.side_effect = [tool("inspect_storage"), tool("inspect_storage"),
        tool("inspect_system"), reply("Storage blocked; RAM is 16 GB."),
        reply("Storage blocked; RAM is 16 GB.")]
    agent.run("Inspect storage and RAM")
    agent.inspect_storage.assert_called_once()
    agent.inspect_system.assert_called_once()
    assert record.call_args.args[2][0]["outcome"] == "denied"


def test_partial_answer_review_can_finish_second_outcome(harness):
    agent, client, record, show = harness
    client.chat.side_effect = [tool("inspect_storage"), reply("Example SSD"),
        tool("inspect_system"), reply("Example SSD and 16 GB RAM"),
        reply("Example SSD and 16 GB RAM")]
    agent.run("Identify SSD and RAM")
    show.assert_called_once_with("Example SSD and 16 GB RAM")
    checkpoint = client.chat.call_args_list[2].kwargs["messages"]
    assert any("EACH requested outcome" in m.get("content", "") for m in checkpoint)


def test_legitimate_clarification_ends_after_one_review(harness):
    agent, client, record, show = harness
    client.chat.side_effect = [reply("Which folder should I inspect?"),
                               reply("Which folder should I inspect?")]
    agent.run("Inspect that folder")
    assert client.chat.call_count == 2
    show.assert_called_once_with("Which folder should I inspect?")


def test_recovery_respects_tool_budget(harness, capsys):
    agent, client, record, show = harness
    agent.args.max_tools = 2
    agent.inspect_storage.side_effect = ValueError("Unavailable")
    client.chat.side_effect = [tool("inspect_storage") for _ in range(3)]
    agent.run("Identify SSD")
    assert agent.inspect_storage.call_count == 2
    show.assert_not_called()
    assert "tool-call limit" in capsys.readouterr().out
    assert "without a verified final answer" in record.call_args.args[1]


def test_emergency_stop_prevents_model_and_tools(harness):
    agent, client, record, show = harness
    agent.check.side_effect = KeyboardInterrupt
    agent.run("Identify SSD")
    client.chat.assert_not_called()
    agent.inspect_storage.assert_not_called()
    assert agent.stop.is_set()


@pytest.mark.parametrize("result, expected", [
    ({"ok": False}, "error"), ({"errors": ["No device"]}, "error"),
    ({"verified": False}, "unverified"), ({"denied": True}, "denied"),
    ({"errors": [], "ok": True}, "returned")])
def test_failure_evidence_classification(result, expected):
    assert agent_runtime.outcome(result) == expected
