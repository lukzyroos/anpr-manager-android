"""
config_store.py (wersja mobilna)
---------------------------------
Zapis i odczyt listy profili kamer w folderze danych aplikacji
Kivy/Android (App.user_data_dir) - odpowiednik %APPDATA% na Windows,
ale działający też na Androidzie (prywatny folder aplikacji, nie
wymaga żadnych dodatkowych uprawnień systemowych).
"""

import json
import os
import base64
from dataclasses import asdict
from typing import List
from isapi_client import CameraProfile


def _config_dir() -> str:
    try:
        from kivy.app import App
        app = App.get_running_app()
        if app is not None:
            base = app.user_data_dir
            os.makedirs(base, exist_ok=True)
            return base
    except Exception:
        pass
    # Awaryjnie (np. przy testach poza Kivy) - katalog domowy
    base = os.path.expanduser("~")
    os.makedirs(base, exist_ok=True)
    return base


def _config_file() -> str:
    return os.path.join(_config_dir(), "cameras.json")


def _obfuscate(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _deobfuscate(text: str) -> str:
    try:
        return base64.b64decode(text.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def load_cameras() -> List[CameraProfile]:
    path = _config_file()
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (json.JSONDecodeError, OSError):
        return []

    cameras = []
    for item in raw:
        try:
            cameras.append(CameraProfile(
                name=item["name"],
                ip=item["ip"],
                port=int(item["port"]),
                username=item["username"],
                password=_deobfuscate(item["password"]),
                use_https=bool(item.get("use_https", False)),
                channel_id=item.get("channel_id", "1"),
            ))
        except (KeyError, ValueError):
            continue
    return cameras


def save_cameras(cameras: List[CameraProfile]) -> None:
    raw = []
    for cam in cameras:
        d = asdict(cam)
        d["password"] = _obfuscate(cam.password)
        raw.append(d)
    with open(_config_file(), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=2)
