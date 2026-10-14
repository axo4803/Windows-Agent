"""Structured file actions that never delete user data or overwrite files destructively."""
import os
from pathlib import Path
import shutil
import time
from PIL import Image, ImageFilter, ImageOps

EXECUTABLE_EXTENSIONS = {
    '.exe', '.dll', '.com', '.bat', '.cmd', '.ps1', '.vbs', '.js',
    '.lnk', '.scr', '.msi', '.reg', '.sys', '.drv', '.hta', '.cpl'
}
SAFE_TEXT_EXTENSIONS = {
    '.txt', '.md', '.csv', '.tsv', '.json', '.yaml', '.yml', '.toml',
    '.ini', '.cfg', '.conf', '.xml', '.html', '.css', '.log', '.rst', '.sql'
}
SAFE_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}


def destination(path):
    target = Path(path).expanduser().resolve()
    forbidden = [
        Path(os.environ.get('SystemRoot', r'C:\Windows')),
        Path(os.environ.get('ProgramFiles', r'C:\Program Files')),
        Path(__file__).resolve().parent,
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
