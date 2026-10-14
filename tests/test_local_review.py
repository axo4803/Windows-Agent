import json
from unittest.mock import Mock

import pytest

import agent_runtime


@pytest.mark.parametrize("payload", [
    {"text": '\\"\n' * 4000},
    {"items": [{"text": "x" * 4000} for _ in range(30)]},
    ["x" * 4000] * 20,
])
@pytest.mark.parametrize("limit", [64, 700, 3500, 5000])
def test_context_obeys_serialized_budget(payload, limit):
    encoded = agent_runtime.bounded_result(payload, limit)
    assert len(encoded) <= limit
    assert json.loads(encoded)


def test_context_recurses_into_short_lists_without_mutation():
    original = {"items": [{"text": "x" * 6000}], "verified": False}
    result = json.loads(agent_runtime.bounded_result(original))
    assert result["verified"] is False
    assert result["items"][0]["text"].endswith("[truncated]")
    assert result["_context_omissions"]
    assert len(original["items"][0]["text"]) == 6000


def test_dry_run_does_not_activate_window(monkeypatch):
    import ctypes
    from screen_use.tools import ScreenUse
    from screen_use.perception.uia_tree import UIElement
    user32 = Mock()
    monkeypatch.setattr(ctypes.windll, "user32", user32)
    tools = ScreenUse(dry_run=True)
    tools._elements = [UIElement(id=1, name="Button", control_type="ButtonControl",
                                bbox=(0, 0, 20, 20), window_handle=123)]
    assert tools.click_element_id(1)["dry_run"] is True
    assert not user32.mock_calls


@pytest.mark.parametrize("entries", [{}, {"app:test": {"name": "Calculator", "app_id": "test"}}])
def test_app_search_cache_expires_and_honors_stop(monkeypatch, entries):
    import desktop_agent
    agent = desktop_agent.DesktopAgent.__new__(desktop_agent.DesktopAgent)
    agent.check = Mock()
    agent.discovered_apps = {}
    agent._app_catalog_refreshed = None
    clock = [0]
    monkeypatch.setattr(desktop_agent.time, "monotonic", lambda: clock[0])
    catalog = Mock(return_value=entries)
    monkeypatch.setattr(desktop_agent.app_catalog, "catalog", catalog)
    agent.find_applications("calc")
    agent.find_applications("other")
    assert catalog.call_count == 1
    clock[0] = 30
    agent.find_applications("calc")
    assert catalog.call_count == 2
    agent.check.side_effect = KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        agent.find_applications("calc")
    assert catalog.call_count == 2
