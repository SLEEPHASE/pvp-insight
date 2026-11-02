"""PVP Insight 网页版：本地 HTTP 服务。"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from pvp_insight import __version__
from pvp_insight.config import (
    APP_DIR,
    Settings,
    find_steam_id,
    is_steam_id64,
    load_settings,
    save_settings,
    setup_logging,
)

from .services import LoginService, MatchService, ProfileService
from .avatars import AvatarService
from .state import WebState

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
ASSET_ICON = Path(__file__).resolve().parent.parent / "assets" / "icon.png"


class WebApp:
    def __init__(self, port: int, demo: bool = False):
        self.port = port
        self.demo = demo
        self.settings: Settings = load_settings()
        if demo:
            self.settings.steam_id = "76561198000000001"
            self.settings.nickname = "DEMO"
        elif not self.settings.steam_id:
            detected = find_steam_id()
            if detected:
                self.settings.steam_id = detected
                save_settings(self.settings)
        self.state = WebState(self.settings, demo=demo)
        if demo:
            from .demo import demo_match, demo_profiles

            self.state.set_match(demo_match())
            for profile in demo_profiles().values():
                self.state.set_profile(profile)
            self.state.set_status("live", "演示模式 · 对局进行中")
        self.profiles = ProfileService(self.state)
        self.avatars = AvatarService(self.state)
        self.login = LoginService(self.settings, self._on_login)
        self.match = MatchService(self.state, self._on_roster)
        self.roster_lock = threading.Lock()

    def start(self) -> None:
        if self.demo:
            return
        if self.settings.steam_id:
            self.state.set_status("connecting", "正在连接完美平台…")
            self.match.start(self.settings.steam_id)
        else:
            self.state.set_status("error", "未读取到 SteamID，请在设置中填写")

    def stop(self) -> None:
        self.match.stop()
        self.login.cancel()

    # ------------------------------------------------------------------ 回调

    def _on_roster(self, match: dict, players: list[dict]) -> None:
        with self.roster_lock:
            self.profiles.refresh(self.settings, players)

    def _on_login(self, data: dict) -> None:
        self.settings.token = str(data.get("token") or "")
        self.settings.account_steam_id = str(data.get("steam_id") or "")
        self.settings.nickname = str(data.get("nickname") or "")
        self.settings.avatar = str(data.get("avatar") or "")
        if not self.settings.steam_id and self.settings.account_steam_id:
            self.settings.steam_id = self.settings.account_steam_id
        save_settings(self.settings)
        self.state.clear_auth_error()
        if self.settings.steam_id and self.match.steam_id != self.settings.steam_id:
            self.match.start(self.settings.steam_id)
        if self.state.match:
            self.profiles.refresh(
                self.settings,
                list(self.state.match.get("players") or []),
                force=True,
            )

    # ------------------------------------------------------------------ API

    def api_state(self) -> dict:
        data = self.state.snapshot()
        data["server"] = {
            "version": __version__,
            "port": self.port,
            "steam_detected": bool(self.settings.steam_id),
            "poll_interval": 2.0,
        }
        data["login"] = self.login.snapshot()
        return data

    def api_settings(self, payload: dict) -> tuple[int, dict]:
        sid = str(payload.get("steam_id") or "").strip()
        if sid and not is_steam_id64(sid):
            return 400, {"ok": False, "error": "SteamID 应为以 7656 开头的 17 位数字"}
        old = self.settings.steam_id
        self.settings.steam_id = sid
        save_settings(self.settings)
        if sid != old:
            self.state.clear_match()
            self.profiles.clear()
            self.match.start(sid)
        return 200, {"ok": True, "steam_id": sid}

    def api_logout(self) -> dict:
        self.settings.token = ""
        self.settings.account_steam_id = ""
        self.settings.nickname = ""
        self.settings.avatar = ""
        save_settings(self.settings)
        self.state.clear_auth_error()
        if self.state.match:
            self.profiles.refresh(
                self.settings,
                list(self.state.match.get("players") or []),
                force=True,
            )
        return {"ok": True}

    def api_refresh(self) -> dict:
        if self.state.match:
            self.profiles.refresh(
                self.settings,
                list(self.state.match.get("players") or []),
                force=True,
            )
        return {"ok": True}


class Handler(BaseHTTPRequestHandler):
    server_version = f"PVPInsight/{__version__}"
    app: WebApp

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802
        # 本地服务不打印每个请求，避免刷屏
        if args and str(args[0]).startswith(("4", "5")):
            log.warning("%s - %s", self.address_string(), fmt % args)

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str,
        cache: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' https: data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; font-src 'self' data:;",
        )
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _json(self, status: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        self._send(status, body, "application/json; charset=utf-8")

    def _static(self, path: str) -> None:
        if path in ("/", "/index.html"):
            file_path = STATIC_DIR / "index.html"
            content_type = "text/html; charset=utf-8"
        elif path == "/app.js":
            file_path = STATIC_DIR / "app.js"
            content_type = "application/javascript; charset=utf-8"
        elif path == "/style.css":
            file_path = STATIC_DIR / "style.css"
            content_type = "text/css; charset=utf-8"
        elif path in ("/favicon.png", "/icon.png"):
            file_path = STATIC_DIR / "icon.png"
            content_type = "image/png"
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        try:
            body = file_path.read_bytes()
        except OSError:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        self._send(200, body, content_type)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/state":
            self._json(200, self.app.api_state())
            return
        if path == "/api/login/status":
            data = self.app.login.snapshot()
            data["logged_in"] = bool(self.app.settings.token)
            data["nickname"] = self.app.settings.nickname
            self._json(200, data)
            return
        if path == "/api/health":
            self._json(200, {"ok": True, "version": __version__})
            return
        if path == "/api/detect":
            sid = find_steam_id()
            self._json(200, {"ok": True, "steam_id": sid})
            return
        if path == "/api/avatar":
            from urllib.parse import parse_qs

            sid = (parse_qs(parsed.query).get("sid") or [""])[0]
            result = self.app.avatars.get(sid)
            if not result:
                self._send(404, b"avatar not found", "text/plain; charset=utf-8")
                return
            body, content_type = result
            self._send(
                200,
                body,
                content_type,
                cache="public, max-age=3600",
            )
            return
        if path.startswith("/api/"):
            self._json(404, {"ok": False, "error": "not found"})
            return
        self._static(path)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        if length > 65536:
            self._json(413, {"ok": False, "error": "payload too large"})
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            self._json(400, {"ok": False, "error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "invalid payload"})
            return

        if path == "/api/settings":
            status, data = self.app.api_settings(payload)
            self._json(status, data)
            return
        if path == "/api/login/start":
            data = self.app.login.start()
            data["logged_in"] = bool(self.app.settings.token)
            data["nickname"] = self.app.settings.nickname
            self._json(200, data)
            return
        if path == "/api/login/cancel":
            self.app.login.cancel()
            self._json(200, {"ok": True})
            return
        if path == "/api/logout":
            self._json(200, self.app.api_logout())
            return
        if path == "/api/refresh":
            self._json(200, self.app.api_refresh())
            return
        self._json(404, {"ok": False, "error": "not found"})


def _free_port(start: int) -> int:
    for port in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError("没有可用的本地端口")


def _existing_instance(port: int) -> str:
    """如果本机已经有 PVP Insight 在跑，直接复用它。"""
    import json as _json
    import urllib.request

    candidates = [port]
    if port != 8765:
        candidates.append(8765)
    for candidate in candidates:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{candidate}/api/health", timeout=0.45
            ) as response:
                data = _json.loads(response.read().decode("utf-8"))
            if data.get("ok") and data.get("version"):
                return f"http://127.0.0.1:{candidate}"
        except Exception:
            continue
    return ""


def main() -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass
    setup_logging(debug="--debug" in sys.argv)
    parser = argparse.ArgumentParser(description="PVP Insight 本地网页版")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--allow-multiple", action="store_true")
    args, _ = parser.parse_known_args()

    if not args.allow_multiple and not args.demo:
        existing = _existing_instance(args.port)
        if existing:
            print(f"PVP Insight 已在运行：{existing}")
            if not args.no_browser:
                webbrowser.open(existing)
            return 0

    port = _free_port(args.port)
    app = WebApp(port, demo=args.demo)
    handler = type("BoundHandler", (Handler,), {"app": app})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True

    url = f"http://127.0.0.1:{port}"
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        (APP_DIR / "web_url.txt").write_text(url, encoding="utf-8")
    except OSError:
        pass
    print("=" * 62)
    print("  PVP Insight · 完美平台对局情报（本地网页版）")
    print(f"  本机地址: {url}")
    print("  游戏内：Shift+Tab 打开 Steam 覆盖层 → 浏览器 → 输入上面的地址")
    print("  关闭此窗口即停止服务")
    print("=" * 62)

    app.start()

    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        server.server_close()
        print("服务已停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
