"""Structured file actions that never delete or overwrite existing files."""
import os
from pathlib import Path
import shutil
from PIL import Image, ImageOps

def destination(path):
    target = Path(path).expanduser().resolve()
    forbidden = [Path(os.environ.get('SystemRoot', r'C:\Windows')), Path(os.environ.get('ProgramFiles', r'C:\Program Files')),
                 Path(__file__).resolve().parent]
    if any(target == root or root in target.parents for root in forbidden):
        raise ValueError('System, program and agent files cannot be written through this tool')
    return target

def create_directory(path):
    target = destination(path)
    target.mkdir(parents=True, exist_ok=True)
    return {'created_directory': str(target)}

def copy_file(source, destination_path):
    source = Path(source).expanduser().resolve(strict=True)
    target = destination(destination_path)
    executable_extensions = {'.exe', '.dll', '.com', '.bat', '.cmd', '.ps1', '.vbs', '.js', '.lnk', '.scr', '.msi'}
    if source.suffix.lower() in executable_extensions or target.suffix.lower() in executable_extensions:
        raise ValueError('Copying executable/script/shortcut files is blocked')
    if not source.is_file() or source.stat().st_size > 100_000_000:
        raise ValueError('Source must be a file no larger than 100 MB')
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
    return {'copied': str(source), 'destination': str(target), 'source_retained': True}

def create_text_file(path, text):
    target = destination(path)
    if target.suffix.lower() not in {'.txt', '.md', '.csv', '.json'} or len(text) > 64000:
        raise ValueError('Only txt/md/csv/json files up to 64000 characters can be created')
    with target.open('x', encoding='utf-8') as output:
        output.write(text)
    return {'created_file': str(target), 'characters': len(text)}

def resize_image(source, destination_path, width, height):
    if not isinstance(width, int) or not isinstance(height, int) or not (1 <= width <= 8192 and 1 <= height <= 8192) or width * height > 40000000:
        raise ValueError('Output dimensions must be 1..8192, with at most 40 million pixels')
    source = Path(source).expanduser().resolve(strict=True)
    target = destination(destination_path)
    if target.suffix.lower() != '.png' or source.stat().st_size > 100000000:
        raise ValueError('Output must be a new PNG; source must be at most 100 MB')
    with Image.open(source) as original:
        image = ImageOps.exif_transpose(original).convert('RGB')
        resized = ImageOps.fit(image, (width, height), method=Image.Resampling.LANCZOS)
        with target.open('xb') as output:
            resized.save(output, format='PNG')
    with Image.open(target) as verified:
        verified.verify()
    return {'created_image': str(target), 'width': width, 'height': height,
            'source_retained': True, 'method': 'Lanczos resampling with centered crop; not neural upscaling', 'verified': True}
