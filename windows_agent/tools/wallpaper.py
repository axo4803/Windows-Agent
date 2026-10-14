"""Single-wallpaper action through Windows APIs, without terminal scripts."""
import ctypes
from pathlib import Path
import winreg
from PIL import Image

FIT_MODES = {
    'fill': ('10', '0'),
    'fit': ('6', '0'),
    'stretch': ('2', '0'),
    'center': ('0', '0'),
    'tile': ('0', '1'),
    'span': ('22', '0'),
}


def set_wallpaper(path, check, approve=None, fit='fill'):
    target = Path(path).expanduser().resolve(strict=True)
    if not target.is_file() or target.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.bmp'}:
        raise ValueError('Wallpaper must be an existing PNG/JPEG/BMP file')
    fit_key = (fit or 'fill').strip().lower()
    if fit_key not in FIT_MODES:
        raise ValueError('fit must be one of: ' + ', '.join(FIT_MODES))
    style_val, tile_val = FIT_MODES[fit_key]
    with Image.open(target) as image:
        image.verify()
    if approve is not None:
        approve(f"Set desktop wallpaper to '{target}' using {fit_key.capitalize()} mode.")
    check()
    api = ctypes.WinDLL('user32', use_last_error=True).SystemParametersInfoW
    api.argtypes = [ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32]
    api.restype = ctypes.c_int
    prev_buf = ctypes.create_unicode_buffer(32768)
    prev_ok = api(0x0073, len(prev_buf), ctypes.cast(prev_buf, ctypes.c_void_p), 0)
    previous_wallpaper = prev_buf.value if prev_ok and prev_buf.value else None
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Control Panel\Desktop', 0, winreg.KEY_READ | winreg.KEY_SET_VALUE) as key:
        old = {}
        for name in ('WallpaperStyle', 'TileWallpaper'):
            try:
                old[name] = winreg.QueryValueEx(key, name)[0]
            except FileNotFoundError:
                old[name] = None
        winreg.SetValueEx(key, 'WallpaperStyle', 0, winreg.REG_SZ, style_val)
        winreg.SetValueEx(key, 'TileWallpaper', 0, winreg.REG_SZ, tile_val)
        if not api(0x0014, 0, ctypes.cast(ctypes.c_wchar_p(str(target)), ctypes.c_void_p), 3):
            for name, value in old.items():
                if value is not None:
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
            raise ctypes.WinError(ctypes.get_last_error())
        current = ctypes.create_unicode_buffer(32768)
        read_ok = api(0x0073, len(current), ctypes.cast(current, ctypes.c_void_p), 0)
        fit_state = {name: winreg.QueryValueEx(key, name)[0] for name in old}
    verified = bool(read_ok and Path(current.value).resolve() == target and fit_state == {'WallpaperStyle': style_val, 'TileWallpaper': tile_val})
    return {'applied': True, 'wallpaper': str(target), 'fit': fit_key.capitalize(), 'verified': verified,
            'previous_wallpaper': previous_wallpaper,
            'reported_wallpaper': current.value if read_ok else None,
            'notice': 'Verified Windows wallpaper path and fit configuration, not a screenshot of every monitor.'}
