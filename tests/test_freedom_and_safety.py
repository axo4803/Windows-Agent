"""Tests for maximized task freedom, UX improvements, and non-destructive guardrails."""
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock
from PIL import Image
import pytest

import agent_context
import agent_runtime
import desktop_agent
import file_actions
import terminal_tools


@pytest.fixture(autouse=True)
def _no_real_keyboard(monkeypatch):
    monkeypatch.setattr(desktop_agent.auto, "SendKeys", Mock())
    monkeypatch.setattr(desktop_agent.auto, "GetFocusedControl", Mock(return_value=None))


def _make_agent(read_only=False, strict_approval=False):
    agent = desktop_agent.DesktopAgent.__new__(desktop_agent.DesktopAgent)
    agent.args = SimpleNamespace(
        model="test", read_only=read_only, online_docs=False,
        strict_approval=strict_approval, max_tools=40, max_turns=30,
        max_seconds=600, allow_app=["settings", "taskmgr"]
    )
    agent.calls = 0
    agent.launches = 0
    agent.windows = {}
    agent.elements = {}
    agent.selected = None
    agent.generation = 0
    agent.evidence = []
    agent.launch_windows = {}
    agent.discovered_apps = {}
    agent._app_catalog_refreshed = None
    agent.apps = {"settings": None, "calculator": ["calc.exe"], "notepad": ["notepad.exe"], "taskmgr": ["Taskmgr.exe"]}
    agent.approved = {"settings", "calculator", "notepad", "taskmgr"} if not read_only else {"settings", "taskmgr"}
    agent.stop = Mock()
    agent.check = Mock()
    agent.approve = Mock()
    agent.process_path = Mock(return_value=r"C:\Program Files\App\app.exe")
    return agent


def test_gpu_inspection_deduplicates_outputs_and_parses_vram(monkeypatch):
    agent = _make_agent()
    monkeypatch.setattr(
        terminal_tools, "run",
        lambda cmd, check: {
            "exit_code": 0,
            "stdout": "0, NVIDIA GeForce RTX 3070, 8192, 1450, 6742, 14, 52, 48.5, 560.94\n",
        }
    )
    res = agent.inspect_gpu()
    assert res["physical_gpu_count"] == 1
    gpu = res["gpus"][0]
    assert gpu["name"] == "NVIDIA GeForce RTX 3070"
    assert gpu["vram_total_mb"] == 8192
    assert gpu["vram_total_gb"] == 8.0
    assert gpu["vram_used_mb"] == 1450
    assert gpu["vram_free_mb"] == 6742
    assert gpu["temperature_c"] == 52


def test_terminal_tools_allow_gpu_ollama_wmic_and_block_destructive():
    for cmd in ("nvidia-smi", "nvidia-smi --query-gpu", "ollama list", "ollama ps", "wmic memorychip", "netstat -ano"):
        args = terminal_tools.arguments(cmd)
        assert isinstance(args, list) and len(args) >= 1
    with pytest.raises(ValueError, match="Unsupported terminal command"):
        terminal_tools.arguments("del /f /q C:\\*")
    with pytest.raises(ValueError, match="Unsupported terminal command"):
        terminal_tools.arguments("format C:")


def test_file_actions_non_destructive_conflict_modes_and_move(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("hello", encoding="utf-8")

    with pytest.raises(FileExistsError):
        file_actions.create_text_file(str(src), "new", on_conflict="error")

    r_rename = file_actions.create_text_file(str(src), "second", on_conflict="rename")
    assert r_rename["created_file"].endswith("notes_1.txt")
    assert src.read_text(encoding="utf-8") == "hello"

    r_backup = file_actions.create_text_file(str(src), "updated", on_conflict="backup")
    assert "backup_created" in r_backup
    assert src.read_text(encoding="utf-8") == "updated"

    file_actions.create_text_file(str(src), "!", on_conflict="append")
    assert src.read_text(encoding="utf-8") == "updated!"

    sub = tmp_path / "archive"
    moved = file_actions.move_file(r_rename["created_file"], str(sub))
    assert moved["verified"] is True
    assert (sub / "notes_1.txt").read_text(encoding="utf-8") == "second"


def test_file_actions_block_executables_and_protected_paths(tmp_path):
    bad = tmp_path / "script.ps1"
    bad.write_text("Write-Host hi", encoding="utf-8")
    with pytest.raises(ValueError, match="blocked"):
        file_actions.copy_file(str(bad), str(tmp_path / "copy.ps1"))
    with pytest.raises(ValueError, match="blocked"):
        file_actions.move_file(str(bad), str(tmp_path / "moved.ps1"))
    with pytest.raises(ValueError, match="cannot be written"):
        file_actions.destination(str(file_actions.Path(__file__).resolve().parent.parent / "agent.py"))


def test_resize_image_supports_pad_contain_crop_and_formats(tmp_path):
    src = tmp_path / "source_4_3.png"
    Image.new("RGB", (400, 300), (30, 120, 200)).save(src, format="PNG")

    out_pad = tmp_path / "padded_16_9.png"
    res_pad = file_actions.resize_image(str(src), str(out_pad), 1600, 900, mode="pad")
    assert res_pad["width"] == 1600 and res_pad["height"] == 900
    assert res_pad["mode"] == "pad" and res_pad["verified"] is True

    out_jpg = tmp_path / "cropped.jpg"
    res_jpg = file_actions.resize_image(str(src), str(out_jpg), 800, 450, mode="crop")
    assert res_jpg["width"] == 800 and res_jpg["height"] == 450


def test_search_files_and_offset_read(tmp_path):
    sub = tmp_path / "photos" / "2026"
    sub.mkdir(parents=True)
    img = sub / "Guilin_River.png"
    img.write_bytes(b"12345")
    txt = tmp_path / "large.txt"
    txt.write_text("A" * 70000 + "TAIL", encoding="utf-8")

    found = agent_context.search_files(str(tmp_path), "guilin", lambda: None)
    assert len(found["matches"]) == 1
    assert found["matches"][0]["name"] == "Guilin_River.png"

    agent = _make_agent()
    chunk1 = agent.read_text_file(str(txt), offset=0)
    assert chunk1["truncated"] is True and chunk1["bytes_read"] == 65536
    chunk2 = agent.read_text_file(str(txt), offset=70000)
    assert chunk2["text"] == "TAIL" and chunk2["truncated"] is False


def test_ui_clicks_and_typing_avoid_approval_fatigue_while_blocking_destructive(monkeypatch):
    agent = _make_agent(read_only=False, strict_approval=False)
    win = MagicMock()
    win.Name = "Upscayl"
    win.NativeWindowHandle = 999
    win.ProcessId = 1234
    win.ClassName = "Chrome_WidgetWin_1"
    win.GetRuntimeId.return_value = (1, 2)
    agent.selected = win
    monkeypatch.setattr(desktop_agent.ctypes.windll.user32, "GetForegroundWindow", lambda: 999)

    def _ctrl(name, kind="ButtonControl", aid=""):
        c = MagicMock()
        c.Name = name
        c.ControlTypeName = kind
        c.AutomationId = aid
        c.IsOffscreen = False
        c.IsEnabled = True
        c.IsPassword = False
        c.GetRuntimeId.return_value = (1, 2)
        c.GetPattern.return_value = None
        return c

    agent.elements["1:1"] = (_ctrl("Save", "ButtonControl"), "Save", "ButtonControl", "")
    res = agent.click_element("1:1")
    assert res["clicked"] == "Save"
    agent.approve.assert_not_called()

    agent.elements["1:2"] = (_ctrl("Purchase", "ButtonControl"), "Purchase", "ButtonControl", "")
    agent.click_element("1:2")
    agent.approve.assert_called_once()

    agent.elements["1:3"] = (_ctrl("Delete All Files", "ButtonControl"), "Delete All Files", "ButtonControl", "")
    with pytest.raises(ValueError, match="destructive"):
        agent.click_element("1:3")

    agent.approve.reset_mock()
    edit = _ctrl("Output Folder", "EditControl")
    vp = MagicMock()
    vp.Value = ""
    edit.GetPattern.return_value = vp
    agent.elements["1:4"] = (edit, "Output Folder", "EditControl", "")
    typed = agent.type_text("1:4", r"C:\Users\kuin3\Pictures")
    assert typed["typed_characters"] > 0
    agent.approve.assert_not_called()


def test_press_keys_allows_navigation_and_blocks_destructive(monkeypatch):
    agent = _make_agent(read_only=False)
    win = MagicMock()
    win.Name = "Notepad"
    win.NativeWindowHandle = 555
    win.ProcessId = 100
    agent.selected = win
    monkeypatch.setattr(desktop_agent.ctypes.windll.user32, "GetForegroundWindow", lambda: 555)
    agent._focused_control = Mock(return_value=(Mock(), "Document", "DocumentControl"))

    res = agent.press_keys("ctrl+s")
    assert res["pressed"] == "ctrl+s"

    with pytest.raises(ValueError, match="Unsupported or blocked key"):
        agent.press_keys("delete")
    with pytest.raises(ValueError, match="Unsupported or blocked key"):
        agent.press_keys("alt+f4")


def test_launch_application_resolves_by_name_and_reuses_existing_window(monkeypatch):
    agent = _make_agent(read_only=False)
    monkeypatch.setattr(desktop_agent.app_catalog, "catalog", lambda check: {
        "app:crystal123": {"name": "CrystalDiskInfo", "app_id": "CrystalDiskInfo.App"}
    })
    launched = []
    monkeypatch.setattr(desktop_agent.app_catalog, "launch", lambda entry: launched.append(entry["name"]))
    monkeypatch.setattr(agent, "discover_applications", lambda: {"windows": [], "start_menu_applications": []})
    res = agent.launch_application("CrystalDiskInfo")
    assert res["launch_requested"] == "CrystalDiskInfo"
    assert launched == ["CrystalDiskInfo"]


def _focused_agent(monkeypatch, name="Document", kind="DocumentControl", strict=False):
    agent = _make_agent(strict_approval=strict)
    win = MagicMock()
    win.Name = "Notepad"
    win.NativeWindowHandle = 555
    win.ProcessId = 100
    win.ClassName = "Notepad"
    win.GetRuntimeId.return_value = (1, 2)
    control = MagicMock()
    control.Name = name
    control.ControlTypeName = kind
    control.AutomationId = ""
    control.IsOffscreen = False
    control.IsEnabled = True
    control.IsPassword = False
    control.GetRuntimeId.return_value = (1, 2, 3)
    control.GetParentControl.return_value = win
    control.GetPattern.return_value = None
    agent.selected = win
    agent.elements["1:1"] = (control, name, kind, "")
    monkeypatch.setattr(desktop_agent.ctypes.windll.user32, "GetForegroundWindow", lambda: 555)
    desktop_agent.auto.GetFocusedControl.return_value = control
    return agent, control


def test_text_fallback_escapes_shortcuts_and_appends_at_end(monkeypatch):
    agent, control = _focused_agent(monkeypatch)
    agent.type_text("1:1", "{Ctrl}a{Delete}", append=True)
    assert desktop_agent.auto.SendKeys.call_args_list[0].args == ("{Ctrl}{End}",)
    desktop_agent.auto.SendKeys.assert_called_with("{{}Ctrl{}}a{{}Delete{}}", charMode=True)


def test_unknown_existing_text_requires_approval_before_replacement(monkeypatch):
    agent, control = _focused_agent(monkeypatch)
    agent.approve.side_effect = PermissionError("Action denied by user")
    with pytest.raises(PermissionError):
        agent.type_text("1:1", "replacement")
    control.SetFocus.assert_not_called()
    desktop_agent.auto.SendKeys.assert_not_called()


@pytest.mark.parametrize("key,name,kind,strict", [
    ("enter", "Input", "EditControl", True),
    ("space", "Purchase", "ButtonControl", False),
    ("ctrl+s", "Document", "DocumentControl", True),
])
def test_keyboard_approval_denial_prevents_input(monkeypatch, key, name, kind, strict):
    agent, _ = _focused_agent(monkeypatch, name, kind, strict)
    agent.approve.side_effect = PermissionError("Action denied by user")
    with pytest.raises(PermissionError):
        agent.press_keys(key)
    agent.approve.assert_called_once()
    desktop_agent.auto.SendKeys.assert_not_called()


@pytest.mark.parametrize("tool,args", [("press_keys", ("space",)), ("scroll_window", ("down",))])
@pytest.mark.parametrize("blocked", ["password", "destructive", "console"])
def test_keyboard_and_scroll_validate_focused_control(monkeypatch, tool, args, blocked):
    agent, control = _focused_agent(monkeypatch)
    if blocked == "password":
        control.IsPassword = True
    elif blocked == "destructive":
        control.Name = "Delete All Files"
    else:
        agent.process_path.return_value = r"C:\Windows\System32\cmd.exe"
    with pytest.raises(ValueError):
        getattr(agent, tool)(*args)
    desktop_agent.auto.SendKeys.assert_not_called()


def test_scroll_uses_uia_pattern_without_keyboard(monkeypatch):
    agent, control = _focused_agent(monkeypatch, "Options", "ComboBoxControl")
    pattern = Mock(VerticallyScrollable=True)
    pattern.Scroll.return_value = True
    control.GetPattern.return_value = pattern
    result = agent.scroll_window("page_down", 2)
    assert result["scrolled"] == "page_down"
    assert pattern.Scroll.call_count == 2
    pattern.Scroll.assert_called_with(desktop_agent.auto.ScrollAmount.NoAmount,
                                     desktop_agent.auto.ScrollAmount.LargeIncrement)
    desktop_agent.auto.SendKeys.assert_not_called()


def test_scroll_refuses_missing_pattern(monkeypatch):
    agent, control = _focused_agent(monkeypatch)
    agent.selected.GetPattern.return_value = None
    with pytest.raises(ValueError, match="No supported vertical ScrollPattern"):
        agent.scroll_window("down")
    desktop_agent.auto.SendKeys.assert_not_called()


def test_keyboard_focus_change_during_approval_blocks_input(monkeypatch):
    agent, control = _focused_agent(monkeypatch)
    changed = MagicMock()
    changed.GetRuntimeId.return_value = (9, 9)
    agent._focused_control = Mock(side_effect=[(control, "Document", "DocumentControl"),
                                              (changed, "Other", "DocumentControl")])
    with pytest.raises(ValueError, match="focused control changed"):
        agent.press_keys("enter")
    desktop_agent.auto.SendKeys.assert_not_called()


def test_strict_file_write_denial_prevents_creation(tmp_path):
    agent = _make_agent(strict_approval=True)
    agent.approve.side_effect = PermissionError("Action denied by user")
    target = tmp_path / "new.txt"
    with pytest.raises(PermissionError):
        agent.create_text_file(str(target), "hello")
    assert not target.exists()


def test_fallback_typing_refuses_wrong_focus(monkeypatch):
    agent, control = _focused_agent(monkeypatch)
    other = MagicMock()
    other.GetRuntimeId.return_value = (9, 9)
    agent._focused_control = Mock(return_value=(other, "Other", "DocumentControl"))
    with pytest.raises(ValueError, match="did not receive focus"):
        agent.type_text("1:1", "hello", append=True)
    desktop_agent.auto.SendKeys.assert_not_called()
