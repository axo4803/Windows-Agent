"""Ensure packaging moves do not redirect saved data or weaken path protection."""
from pathlib import Path

import pytest

from windows_agent import PROJECT_ROOT, context
from windows_agent.tools import files, system


def test_saved_data_stays_in_original_project_memory_directory():
    project = Path(__file__).resolve().parent.parent
    assert PROJECT_ROOT == project
    assert system.ROOT == project / 'memory'
    assert system.PROFILE == project / 'memory' / 'system-profile.json'
    assert context.HISTORY == project / 'memory' / 'conversation.json'


@pytest.mark.parametrize('relative', ['README.md', 'desktop_agent.py', 'windows_agent/tools/files.py'])
def test_file_tools_protect_entire_project_after_module_move(relative):
    project = Path(__file__).resolve().parent.parent
    with pytest.raises(ValueError, match='cannot be written'):
        files.destination(project / relative)
