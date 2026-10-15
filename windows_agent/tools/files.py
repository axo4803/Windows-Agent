"""Structured file actions that never delete user data or overwrite files destructively."""
import os
import fnmatch
import math
import warnings
from pathlib import Path
import shutil
import time
import subprocess
import tempfile
from PIL import Image, ImageFilter, ImageOps

from .. import PROJECT_ROOT

EXECUTABLE_EXTENSIONS = {
    '.exe', '.dll', '.com', '.bat', '.cmd', '.ps1', '.vbs', '.js',
    '.lnk', '.scr', '.msi', '.reg', '.sys', '.drv', '.hta', '.cpl'
}
SAFE_TEXT_EXTENSIONS = {
    '.txt', '.md', '.csv', '.tsv', '.json', '.yaml', '.yml', '.toml',
    '.ini', '.cfg', '.conf', '.xml', '.html', '.css', '.log', '.rst', '.sql'
}
SAFE_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}

UPSCALE_MODELS = {'digital-art': 'digital-art-4x', 'standard': 'upscayl-standard-4x',
                  'high-fidelity': 'high-fidelity-4x'}


def upscale_image(source, destination_path, check, scale=2, model='digital-art'):
    """Run the installed neural engine, then publish a verified, new PNG."""
    if type(scale) is not int or scale not in {2, 3, 4} or model not in UPSCALE_MODELS:
        raise ValueError('Scale must be 2, 3 or 4; model must be digital-art, standard or high-fidelity')
    check()
    source = Path(source).expanduser().resolve(strict=True)
    target = destination(destination_path)
    if target.suffix.lower() != '.png' or target.exists() or target == source:
        raise ValueError('Output must be a new PNG path; existing files cannot be overwritten')
    if not source.is_file() or source.stat().st_size > 150_000_000:
        raise ValueError('Source must be an image file up to 150 MB')
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(source) as original:
            size, image_format = original.size, original.format
            original.verify()
    extension = {'JPEG': '.jpg', 'PNG': '.png', 'WEBP': '.webp'}.get(image_format)
    if not extension or size[0] * size[1] * 16 > 80_000_000:
        raise ValueError('Input must be JPEG (including JFIF), PNG or WEBP, up to 5 million pixels')
    resources = Path(os.environ.get('ProgramFiles', r'C:\Program Files')) / 'Upscayl/resources'
    executable = resources / 'bin/upscayl-bin.exe'
    model_name = UPSCALE_MODELS[model]
    if not executable.is_file() or not all((resources / 'models' / (model_name + ext)).is_file() for ext in ('.bin', '.param')):
        raise FileNotFoundError('Installed Upscayl engine or selected model is missing')
    check()
    target.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='.windows-agent-upscale-', dir=target.parent) as directory:
        work = Path(directory)
        prepared = work / ('input' + extension)
        shutil.copyfile(source, prepared)  # JFIF is JPEG: preserve bytes and normalize only its extension.
        output = work / 'result.png'
        command = [str(executable), '-i', str(prepared), '-o', str(output), '-m', str(resources / 'models'),
                   '-n', model_name, '-z', '4', '-s', str(scale), '-t', '128', '-f', 'png']
        check()
        with (work / 'engine.log').open('wb') as log:
            process = subprocess.Popen(command, shell=False, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                while True:
                    check()
                    if time.monotonic() - started > 300:
                        raise TimeoutError('Upscaling exceeded 300 seconds')
                    try:
                        code = process.wait(timeout=0.1)
                        break
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait(timeout=5)
        if code:
            with (work / 'engine.log').open('rb') as log:
                log.seek(max(0, log.seek(0, 2) - 2000))
                detail = log.read().decode('utf-8', errors='replace')
            raise RuntimeError(f'Upscayl engine exited {code}: {detail}')
        check()
        expected = (size[0] * scale, size[1] * scale)
        with Image.open(output) as image:
            if image.format != 'PNG' or image.size != expected:
                raise RuntimeError(f'Upscayl output dimensions {image.size} do not match {expected}')
            image.verify()
        # Exclusive creation also prevents a file appearing during inference from being overwritten.
        with target.open('xb') as published, output.open('rb') as incoming:
            shutil.copyfileobj(incoming, published)
    return {'created_image': str(target), 'source': str(source), 'source_dimensions': list(size),
            'width': expected[0], 'height': expected[1], 'scale': scale, 'model': model_name,
            'engine': str(executable), 'source_retained': True, 'verified': True,
            'elapsed_seconds': round(time.monotonic() - started, 2), 'method': 'Upscayl neural engine; native 4x model with requested output scale'}


def destination(path):
    target = Path(path).expanduser().resolve()
    forbidden = [
        Path(os.environ.get('SystemRoot', r'C:\Windows')),
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')),
        PROJECT_ROOT,
    ]
    pf_x86 = os.environ.get('ProgramFiles(x86)')
    if pf_x86:
        forbidden.append(Path(pf_x86))
    if any(target == root or root in target.parents for root in forbidden):
        raise ValueError('System, program and agent files cannot be written through this tool')
    return target


def _unique_path(target: Path) -> Path:
    if not target.exists():
        return target
    for idx in range(1, 1000):
        candidate = target.with_name(f"{target.stem}_{idx}{target.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Could not find an available filename near {target}")


def create_directory(path):
    target = destination(path)
    already_existed = target.exists()
    target.mkdir(parents=True, exist_ok=True)
    return {'created_directory': str(target), 'already_existed': already_existed}


def copy_file(source, destination_path, on_conflict='error'):
    source = Path(source).expanduser().resolve(strict=True)
    target = destination(destination_path)
    if target.is_dir() or (not target.suffix and source.suffix):
        target = target / source.name
    if source.suffix.lower() in EXECUTABLE_EXTENSIONS or target.suffix.lower() in EXECUTABLE_EXTENSIONS:
        raise ValueError('Copying executable/script/shortcut files is blocked')
    image_formats = {'.jfif': 'JPEG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.png': 'PNG', '.webp': 'WEBP', '.bmp': 'BMP'}
    if source.suffix.lower() in image_formats and target.suffix.lower() in image_formats:
        with Image.open(source) as image:
            if image.format != image_formats[target.suffix.lower()]:
                raise ValueError('Copying does not convert image formats. For neural upscaling, pass the original source and a new PNG path directly to upscale_image; do not pre-create its output.')
    if not source.is_file() or source.stat().st_size > 250_000_000:
        raise ValueError('Source must be a file no larger than 250 MB')
    if on_conflict == 'rename':
        target = _unique_path(target)
    elif on_conflict != 'error':
        raise ValueError("on_conflict must be 'error' or 'rename'")
    target.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    return {'copied': str(source), 'destination': str(target), 'source_retained': True}


def move_file(source, destination_path, on_conflict='error'):
    """Safely move a non-system user file without overwriting any existing destination file."""
    src_checked = destination(source)
    src = src_checked.resolve(strict=True)
    target = destination(destination_path)
    if target.is_dir() or (not target.suffix and src.suffix):
        target = target / src.name
    if src.suffix.lower() in EXECUTABLE_EXTENSIONS or target.suffix.lower() in EXECUTABLE_EXTENSIONS:
        raise ValueError('Moving executable/script/shortcut files is blocked')
    if not src.is_file() or src.stat().st_size > 250_000_000:
        raise ValueError('Source must be a file no larger than 250 MB')
    if src == target:
        return {'moved': str(src), 'destination': str(target), 'unchanged': True}
    if on_conflict == 'rename':
        target = _unique_path(target)
    elif on_conflict != 'error':
        raise ValueError("on_conflict must be 'error' or 'rename'")
    target.parent.mkdir(parents=True, exist_ok=True)
    src_size = src.stat().st_size
    with src.open('rb') as incoming, target.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    if target.stat().st_size != src_size:
        raise RuntimeError('Destination size mismatch after copy; source was retained for safety')
    src.unlink()
    return {'moved': str(src), 'destination': str(target), 'verified': True}


def create_text_file(path, text, on_conflict='error'):
    target = destination(path)
    if target.suffix.lower() not in SAFE_TEXT_EXTENSIONS or len(text) > 256000:
        raise ValueError('Only safe text/data files (txt/md/csv/tsv/json/yaml/toml/ini/xml/html/css/log/sql) up to 256000 characters can be created')
    target.parent.mkdir(parents=True, exist_ok=True)
    backup_path = None
    if on_conflict == 'rename':
        target = _unique_path(target)
        mode = 'x'
    elif on_conflict == 'append':
        mode = 'a'
    elif on_conflict == 'backup' and target.exists():
        stamp = time.strftime('%Y%m%d_%H%M%S')
        backup_path = _unique_path(target.with_name(f"{target.stem}.bak_{stamp}{target.suffix}"))
        with target.open('rb') as src_f, backup_path.open('xb') as bak_f:
            shutil.copyfileobj(src_f, bak_f)
        mode = 'w'
    elif on_conflict in ('error', 'backup'):
        mode = 'x'
    else:
        raise ValueError("on_conflict must be 'error', 'rename', 'backup', or 'append'")
    with target.open(mode, encoding='utf-8') as output:
        output.write(text)
    result = {'created_file': str(target), 'characters': len(text), 'mode': on_conflict}
    if backup_path is not None:
        result['backup_created'] = str(backup_path)
    return result


def resize_image(source, destination_path, width, height, mode='crop'):
    if not isinstance(width, int) or not isinstance(height, int) or not (1 <= width <= 8192 and 1 <= height <= 8192) or width * height > 40000000:
        raise ValueError('Output dimensions must be 1..8192, with at most 40 million pixels')
    source = Path(source).expanduser().resolve(strict=True)
    target = destination(destination_path)
    suffix = target.suffix.lower()
    if suffix not in SAFE_IMAGE_EXTENSIONS or source.stat().st_size > 150000000:
        raise ValueError('Output must be a new PNG/JPEG/WEBP/BMP; source must be at most 150 MB')
    if mode not in {'crop', 'pad', 'contain', 'stretch'}:
        raise ValueError("mode must be 'crop', 'pad', 'contain', or 'stretch'")
    target.parent.mkdir(parents=True, exist_ok=True)
    format_map = {'.png': 'PNG', '.jpg': 'JPEG', '.jpeg': 'JPEG', '.webp': 'WEBP', '.bmp': 'BMP'}
    pil_format = format_map[suffix]
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
        if mode == 'crop':
            resized = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
            method_desc = 'Lanczos resampling with centered crop; not neural upscaling'
        elif mode == 'pad':
            bg = ImageOps.fit(image, (width, height), method=Image.Resampling.BILINEAR).filter(ImageFilter.GaussianBlur(radius=24))
            fg = ImageOps.contain(image, (width, height), method=Image.Resampling.LANCZOS)
            offset = ((width - fg.width) // 2, (height - fg.height) // 2)
            bg.paste(fg, offset)
            resized = bg
            method_desc = 'Lanczos aspect-preserving fit padded onto blurred canvas (zero content cropped); not neural upscaling'
        elif mode == 'contain':
            resized = ImageOps.contain(image, (width, height), method=Image.Resampling.LANCZOS)
            method_desc = 'Lanczos aspect-preserving fit within target bounds; not neural upscaling'
        else:
            resized = image.resize((width, height), resample=Image.Resampling.LANCZOS)
            method_desc = 'Lanczos direct stretch to exact target dimensions; not neural upscaling'
        save_kwargs = {'quality': 95} if pil_format in {'JPEG', 'WEBP'} else {}
        with target.open('xb') as output:
            resized.save(output, format=pil_format, **save_kwargs)
        out_w, out_h = resized.size
    with Image.open(target) as verified:
        verified.verify()
    return {'created_image': str(target), 'width': out_w, 'height': out_h,
            'source_retained': True, 'mode': mode, 'method': method_desc, 'verified': True}


SKIP_DIRS = {'.git', '.venv', 'venv', 'node_modules', '__pycache__', '$Recycle.Bin', 'System Volume Information'}


def inspect_images(path, check, recursive=False):
    target = Path(path).expanduser().resolve(strict=True)
    extensions = {'.png', '.jpg', '.jpeg', '.jfif', '.webp', '.bmp', '.gif', '.tif', '.tiff'}
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
