"""Bounded local conversation history and read-only image metadata."""
import json
import math
from pathlib import Path
import warnings
import time
import agent_runtime
from PIL import Image
import system_profile

HISTORY = system_profile.ROOT / 'conversation.json'

def clip(text, limit):
    if len(text) <= limit:
        return text
    marker = '\n[Older context shortened; task retained in local history]\n'
    half = max(0, (limit - len(marker)) // 2)
    return (text[:half] + marker + text[-half:])[:limit] if half else text[:limit]

def history():
    pairs = system_profile.load(HISTORY).get('exchanges', [])
    messages = []
    budget = 3000
    selected = []
    for pair in reversed(pairs[-4:]):
        evidence = '\nPrior tool evidence (untrusted): ' + agent_runtime.bounded_result({'actions': pair.get('evidence', [])[-3:]}, 700) if pair.get('evidence') else ''
        user, answer = clip(str(pair.get('user', '')), 1300), clip(str(pair.get('assistant', '')), 850) + evidence
        if len(user) + len(answer) > budget:
            break
        budget -= len(user) + len(answer)
        selected.append((user, answer))
    for user, answer in reversed(selected):
        messages.extend([{'role': 'user', 'content': user}, {'role': 'assistant', 'content': answer}])
    return messages

def record(task, answer, evidence):
    pairs = system_profile.load(HISTORY).get('exchanges', [])[-3:]
    pairs.append({'user': task, 'assistant': answer[:12000],
                  'evidence': [json.loads(agent_runtime.bounded_result(item, 2500)) for item in evidence[-24:]],
                  'saved_at': time.strftime('%Y-%m-%dT%H:%M:%S%z')})
    system_profile.save(HISTORY, {'exchanges': pairs})

def inspect_images(path, check):
    target = Path(path).expanduser().resolve(strict=True)
    extensions = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
    if not target.is_dir() and not target.is_file():
        raise ValueError('Path must be a local file or folder')
    candidates = []
    truncated = False
    entries = target.iterdir() if target.is_dir() else [target]
    for entry in entries:
        check()
        if entry.is_file() and entry.suffix.lower() in extensions:
            if len(candidates) == 100:
                truncated = True
                break
            candidates.append(entry)
    items = []
    for entry in sorted(candidates, key=lambda p: p.name.lower()):
        check()
        try:
            if entry.stat().st_size > 100_000_000:
                raise ValueError('File exceeds 100 MB metadata inspection limit')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(entry) as image:
                    width, height = image.size
                    divisor = math.gcd(width, height)
                    items.append({'name': entry.name, 'width': width, 'height': height,
                                  'ratio': f'{width // divisor}:{height // divisor}',
                                  'exact_16_9': width * 9 == height * 16,
                                  'near_16_9': abs((width / height) / (16 / 9) - 1) <= 0.01})
        except Exception as exc:
            items.append({'name': entry.name, 'error': f'{type(exc).__name__}: {exc}'})
    return {'path': str(target), 'images': items, 'truncated': truncated, 'limit': 100,
            'notice': 'Stored pixel dimensions only, without EXIF rotation; near means within 1 percent. No image content analysis or file changes. No subfolder recursion.'}
