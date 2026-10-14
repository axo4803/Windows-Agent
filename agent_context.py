"""Bounded local conversation history, file search, and read-only image metadata."""
import fnmatch
import json
import math
import os
from pathlib import Path
import time
import warnings
import agent_runtime
from PIL import Image
import system_profile

HISTORY = system_profile.ROOT / 'conversation.json'
SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '$Recycle.Bin', 'System Volume Information'}


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


def inspect_images(path, check, recursive=False):
    target = Path(path).expanduser().resolve(strict=True)
    extensions = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
    if not target.is_dir() and not target.is_file():
        raise ValueError('Path must be a local file or folder')
    candidates = []
    truncated = False
    if target.is_file():
        entries = [target]
    elif recursive:
        entries = []
        for root, dirs, files in os.walk(target):
            check()
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for f in files:
                entries.append(Path(root) / f)
                if len(entries) >= 500:
                    break
            if len(entries) >= 500:
                break
    else:
        entries = target.iterdir()
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
            if entry.stat().st_size > 150_000_000:
                raise ValueError('File exceeds 150 MB metadata inspection limit')
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(entry) as image:
                    width, height = image.size
                    divisor = math.gcd(width, height)
                    item = {'name': entry.name, 'width': width, 'height': height,
                            'ratio': f'{width // divisor}:{height // divisor}',
                            'exact_16_9': width * 9 == height * 16,
                            'near_16_9': abs((width / height) / (16 / 9) - 1) <= 0.01}
                    if recursive and target.is_dir():
                        item['relative_path'] = str(entry.relative_to(target))
                    items.append(item)
        except Exception as exc:
            items.append({'name': entry.name, 'error': f'{type(exc).__name__}: {exc}'})
    return {'path': str(target), 'images': items, 'truncated': truncated, 'limit': 100,
            'recursive': bool(recursive),
            'notice': 'Stored pixel dimensions only, without EXIF rotation; near means within 1 percent. No image content analysis or file changes.'}


def search_files(path, pattern, check, max_depth=4):
    """Fast, bounded non-destructive file and folder search."""
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_dir():
        raise ValueError('Search path must be an existing directory')
    pattern_clean = (pattern or '*').strip()
    is_glob = any(ch in pattern_clean for ch in ('*', '?', '['))
    matches = []
    truncated = False
    base_parts = len(target.parts)
    for root, dirs, files in os.walk(target):
        check()
        root_path = Path(root)
        depth = len(root_path.parts) - base_parts
        if depth >= max_depth:
            dirs[:] = []
        else:
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
        for item_name in sorted(dirs + files, key=str.casefold):
            check()
            matched = (fnmatch.fnmatch(item_name.casefold(), pattern_clean.casefold())
                       if is_glob else pattern_clean.casefold() in item_name.casefold())
            if not matched:
                continue
            full_path = root_path / item_name
            try:
                is_dir = full_path.is_dir()
                size_bytes = None if is_dir else full_path.stat().st_size
            except OSError:
                continue
            matches.append({
                'name': item_name[:260],
                'path': str(full_path),
                'type': 'directory' if is_dir else 'file',
                'size_bytes': size_bytes,
            })
            if len(matches) >= 100:
                truncated = True
                return {'base_directory': str(target), 'pattern': pattern_clean,
                        'matches': matches, 'truncated': truncated, 'limit': 100}
    return {'base_directory': str(target), 'pattern': pattern_clean,
            'matches': matches, 'truncated': truncated, 'limit': 100}
