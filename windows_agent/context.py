"""Bounded local conversation history."""
import json
import time

from . import runtime as agent_runtime
from .tools import system as system_profile

HISTORY = system_profile.ROOT / "conversation.json"


def clip(text, limit):
    if len(text) <= limit:
        return text
    marker = '\n[Older context shortened; task retained in local history]\n'
    half = max(0, (limit - len(marker)) // 2)
    return (text[:half] + marker + text[-half:])[:limit] if half else text[:limit]


def history():
    pairs = system_profile.load(HISTORY).get('exchanges', [])
    messages = []
    budget = 3600
    selected = []
    for pair in reversed(pairs[-5:]):
        evidence = '\nPrior tool evidence (untrusted): ' + agent_runtime.bounded_result({'actions': pair.get('evidence', [])[-4:]}, 800) if pair.get('evidence') else ''
        user, answer = clip(str(pair.get('user', '')), 1400), clip(str(pair.get('assistant', '')), 950) + evidence
        if len(user) + len(answer) > budget:
            break
        budget -= len(user) + len(answer)
        selected.append((user, answer))
    for user, answer in reversed(selected):
        messages.extend([{'role': 'user', 'content': user}, {'role': 'assistant', 'content': answer}])
    return messages


def record(task, answer, evidence):
    pairs = system_profile.load(HISTORY).get('exchanges', [])[-5:]
    pairs.append({'user': task, 'assistant': answer[:12000],
                  'evidence': [json.loads(agent_runtime.bounded_result(item, 2500)) for item in evidence[-32:]],
                  'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')})
    system_profile.save(HISTORY, {'exchanges': pairs})


