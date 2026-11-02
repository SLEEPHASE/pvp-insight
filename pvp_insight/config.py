"""本地配置、日志与 SteamID 自动发现。"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import __app_name__


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _resolve_app_dir() -> Path:
    """源码运行用 AppData；打包版优先用 exe 旁边的 data，便于绿色便携。"""
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "data")
    candidates.append(
        Path(os.environ.get("APPDATA", str(Path.home()))) / __app_name__
    )
    candidates.append(
        Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / __app_name__
    )
    candidates.append(Path(os.environ.get("TEMP", str(Path.home()))) / __app_name__)
    for candidate in candidates:
        if _writable_dir(candidate):
            return candidate
    return candidates[-1]


APP_DIR = _resolve_app_dir()
CONFIG_PATH = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
DEBUG_DIR = APP_DIR / "debug"
CACHE_DIR = APP_DIR / "cache"


def ensure_dirs() -> None:
    for path in (APP_DIR, LOG_DIR, DEBUG_DIR, CACHE_DIR):
        path.mkdir(parents=True, exist_ok=True)


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(data: bytes) -> _DataBlob:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)),
    )


def _dpapi_protect(data: bytes) -> bytes:
    in_blob = _blob(data)
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        "PVP Insight",
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    in_blob = _blob(data)
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def protect_text(text: str) -> str:
    if not text:
        return ""
    try:
        raw = _dpapi_protect(text.encode("utf-8"))
        return "dpapi:" + base64.b64encode(raw).decode("ascii")
    except Exception:
        return "plain:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


def unprotect_text(text: str) -> str:
    if not text:
        return ""
    try:
        if text.startswith("dpapi:"):
            raw = base64.b64decode(text[6:])
            return _dpapi_unprotect(raw).decode("utf-8")
        if text.startswith("plain:"):
            return base64.b64decode(text[6:]).decode("utf-8")
    except Exception:
        return ""
    return text


@dataclass
class Settings:
    # 被监控的玩家（通常自动取自 Steam 登录账号）
    steam_id: str = ""
    # 扫码登录的平台账号
    token: str = ""
    account_steam_id: str = ""
    nickname: str = ""
    avatar: str = ""
    poll_interval: float = 2.0
    overlay_opacity: int = 92
    auto_overlay: bool = False
    overlay_geometry: str = ""
    close_to_tray: bool = False
    first_run: bool = True
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        data = asdict(self)
        data["token"] = protect_text(self.token)
        return data


def load_settings() -> Settings:
    ensure_dirs()
    if not CONFIG_PATH.exists():
        return Settings()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return Settings()
    token = unprotect_text(str(raw.get("token", "")))
    known = {f for f in Settings.__dataclass_fields__ if f != "extra"}
    kwargs = {k: v for k, v in raw.items() if k in known}
    kwargs["token"] = token
    settings = Settings(**kwargs)
    try:
        settings.poll_interval = float(settings.poll_interval)
    except (TypeError, ValueError):
        settings.poll_interval = 2.0
    try:
        settings.overlay_opacity = max(35, min(100, int(settings.overlay_opacity)))
    except (TypeError, ValueError):
        settings.overlay_opacity = 92
    return settings


def save_settings(settings: Settings) -> None:
    ensure_dirs()
    data = settings.to_json()
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(CONFIG_PATH)


def setup_logging(debug: bool = False) -> None:
    ensure_dirs()
    level = logging.DEBUG if debug else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)

    try:
        handler = RotatingFileHandler(
            LOG_DIR / "app.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        )
        root.addHandler(handler)
    except OSError:
        # 另一个实例可能正持有日志文件；不阻塞启动。
        pass

    if debug or sys.stderr:
        console = logging.StreamHandler()
        console.setLevel(level)
        console.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        root.addHandler(console)


def write_debug_json(name: str, data) -> None:
    """保存接口原始响应，便于字段变化时排查（仅保存在本机）。"""
    try:
        ensure_dirs()
        path = DEBUG_DIR / name
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


_STEAM_ID_RE = re.compile(r"^\d{17}$")


def is_steam_id64(value: str) -> bool:
    value = str(value or "").strip()
    return bool(_STEAM_ID_RE.match(value)) and value.startswith("7656")


def steam_id_from_account_id(account_id: int) -> str:
    return str(76561197960265728 + int(account_id))


def _steam_root_candidates() -> list[Path]:
    roots: list[Path] = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
            if value:
                roots.append(Path(value))
    except Exception:
        pass

    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(env)
        if base:
            roots.append(Path(base) / "Steam")

    roots.append(Path("C:/Program Files (x86)/Steam"))
    result: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        try:
            key = str(root.resolve()).lower()
        except Exception:
            key = str(root).lower()
        if key not in seen:
            seen.add(key)
            result.append(root)
    return result


def _parse_login_users(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    best = ""
    best_recent = False
    for match in re.finditer(r'"(\d{17})"\s*\{(.*?)\}', text, re.S):
        sid, block = match.group(1), match.group(2)
        if not sid.startswith("7656"):
            continue
        recent = bool(re.search(r'"MostRecent"\s*"1"', block))
        if recent:
            return sid
        if not best:
            best = sid
    if best_recent:
        return best
    return best


def find_steam_id() -> str:
    """从 Steam 本地配置读取最近登录的 SteamID64。"""
    for root in _steam_root_candidates():
        sid = _parse_login_users(root / "config" / "loginusers.vdf")
        if sid:
            return sid

    # 回退：读注册表里的 ActiveUser（32 位账号 ID）
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Valve\Steam\ActiveProcess",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "ActiveUser")
            if value:
                return steam_id_from_account_id(int(value))
    except Exception:
        pass
    return ""
