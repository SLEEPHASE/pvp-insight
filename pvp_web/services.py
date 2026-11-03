"""后台服务：实时对局、玩家资料、扫码登录。"""

from __future__ import annotations

import asyncio
import base64
import concurrent.futures
import concurrent.futures.thread
import io
import json
import logging
import threading
import time
from concurrent.futures import as_completed
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Callable

import aiohttp
import qrcode

from pvp_insight.api import ApiClient, ApiError, AuthExpired, Profile
from pvp_insight.config import Settings, save_settings

from .protocol import normalize_payload
from .state import WebState

log = logging.getLogger(__name__)

# Python 3.15 惰性导入兼容：asyncio 解析 DNS 时会直接使用这个符号。
concurrent.futures.ThreadPoolExecutor = concurrent.futures.thread.ThreadPoolExecutor
_REAL_EXECUTOR = concurrent.futures.thread.ThreadPoolExecutor


def _qr_data_url(url: str) -> str:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    image = qr.make_image(fill_color="#151b24", back_color="white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


class ProfileCache:
    def __init__(self, ttl: float = 900.0):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.data: dict[str, tuple[float, Profile]] = {}

    def get(self, sid: str) -> Profile | None:
        with self.lock:
            item = self.data.get(str(sid))
            if not item:
                return None
            stamp, profile = item
            if time.time() - stamp > self.ttl:
                return None
            return profile.copy()

    def put(self, profile: Profile) -> None:
        with self.lock:
            self.data[profile.steam_id] = (time.time(), profile.copy())

    def clear(self) -> None:
        with self.lock:
            self.data.clear()


class MatchService:
    def __init__(
        self,
        state: WebState,
        on_roster: Callable[[dict, list[dict]], None],
    ):
        self.state = state
        self.on_roster = on_roster
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.steam_id = ""
        self._last_roster = ""
        self._client = ApiClient()

    def start(self, steam_id: str) -> None:
        self.stop()
        self.steam_id = str(steam_id or "")
        self.stop_event = threading.Event()
        self._last_roster = ""
        if not self.steam_id:
            self.state.set_status("error", "未设置 SteamID")
            return
        self.state.set_status("connecting", "正在连接完美平台…")
        self.thread = threading.Thread(
            target=self._run, name="pvp-match", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        self.thread = None

    # ------------------------------------------------------------------

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_default_executor(_REAL_EXECUTOR(max_workers=4))
        try:
            loop.run_until_complete(self._main())
        except Exception:
            log.exception("match service crashed")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    async def _main(self) -> None:
        delay = 2.0
        while not self.stop_event.is_set():
            try:
                self.state.set_status("connecting", "正在连接完美平台…")
                ws_url = self._client.get_ws_url(self.steam_id)
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=30, connect=10)
                ) as session:
                    async with session.ws_connect(ws_url, heartbeat=25) as ws:
                        delay = 2.0
                        self.state.set_status("waiting", "等待对局开始")
                        await ws.send_str("ping")
                        next_poll = 0.0
                        while not self.stop_event.is_set():
                            now = time.monotonic()
                            if now >= next_poll:
                                await ws.send_json(
                                    {
                                        "messageType": 10001,
                                        "messageData": {"steam_id": self.steam_id},
                                    }
                                )
                                next_poll = now + 2.5
                            try:
                                message = await ws.receive(timeout=1.0)
                            except asyncio.TimeoutError:
                                continue
                            if message.type == aiohttp.WSMsgType.TEXT:
                                text = message.data
                                if text == "pong":
                                    continue
                                try:
                                    frame = json.loads(text)
                                except ValueError:
                                    continue
                                self._frame(frame)
                            elif message.type in (
                                aiohttp.WSMsgType.CLOSED,
                                aiohttp.WSMsgType.CLOSING,
                                aiohttp.WSMsgType.ERROR,
                            ):
                                break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.stop_event.is_set():
                    break
                log.warning("match websocket error: %s", exc)
                self.state.set_status("reconnecting", "连接中断，正在重连…")
                await asyncio.sleep(delay)
                delay = min(30.0, delay * 1.8)

    def _frame(self, frame: dict) -> None:
        message_type = int(frame.get("messageType") or 0)
        data = frame.get("messageData")
        if message_type == 10002 and isinstance(data, dict):
            match = normalize_payload(data)
            if not match:
                return
            self.state.set_match(match)
            self.state.set_status("live", "对局进行中")
            roster = ",".join(sorted(p["sid"] for p in match.get("players") or []))
            if roster and roster != self._last_roster:
                self._last_roster = roster
                self.on_roster(match, list(match.get("players") or []))
        elif message_type == 10003:
            self.state.set_ended()
            self.state.set_status("waiting", "等待对局开始")


class LoginService:
    def __init__(
        self,
        settings: Settings,
        on_success: Callable[[dict], None],
    ):
        self.settings = settings
        self.on_success = on_success
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.status = "idle"
        self.message = ""
        self.qr = ""
        self.nickname = ""

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "status": self.status,
                "message": self.message,
                "qr": self.qr,
                "nickname": self.nickname,
            }

    def start(self) -> dict:
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.snapshot()
            self.status = "starting"
            self.message = "正在申请二维码…"
            self.qr = ""
            self.nickname = ""
            self.stop_event = threading.Event()
            self.thread = threading.Thread(
                target=self._run, name="pvp-login", daemon=True
            )
            self.thread.start()
            return self.snapshot()

    def cancel(self) -> None:
        self.stop_event.set()
        with self.lock:
            if self.status in ("starting", "waiting", "scanned"):
                self.status = "idle"
                self.message = "已取消"
                self.qr = ""

    def _set(self, status: str, message: str = "", qr: str | None = None) -> None:
        with self.lock:
            self.status = status
            self.message = message
            if qr is not None:
                self.qr = qr

    def _run(self) -> None:
        client = ApiClient()
        try:
            access_token, url = client.qr_apply()
            qr = _qr_data_url(url)
            self._set("waiting", "请使用完美世界电竞 APP 扫码", qr)
            for _ in range(60):
                if self.stop_event.is_set():
                    return
                time.sleep(2.0)
                if self.stop_event.is_set():
                    return
                try:
                    result = client.qr_check(access_token)
                except ApiError:
                    continue
                state = int(result.get("status") or 0)
                if state == 1:
                    self._set("scanned", "已扫码，请在手机上确认登录")
                elif state == 2:
                    account = result.get("accountItem") or {}
                    token = str(result.get("token") or "")
                    sid = str(account.get("steamId") or "")
                    if not token or not sid:
                        self._set("error", "登录数据不完整，请重试")
                        return
                    nickname = str(account.get("nickName") or account.get("nickname") or "")
                    avatar = str(account.get("avatar") or "")
                    try:
                        info = client.user_info(token, sid)
                        nickname = str(
                            info.get("nickname")
                            or info.get("nickName")
                            or info.get("pvpNickName")
                            or nickname
                        )
                        avatar = str(info.get("avatar") or info.get("pvpAvatar") or avatar)
                    except Exception:
                        pass
                    data = {
                        "token": token,
                        "steam_id": sid,
                        "nickname": nickname,
                        "avatar": avatar,
                    }
                    self.on_success(data)
                    with self.lock:
                        self.nickname = nickname
                        self.status = "success"
                        self.message = "登录成功"
                        self.qr = ""
                    return
                elif state == 3:
                    self._set("expired", "二维码已过期，请刷新")
                    return
            self._set("expired", "扫码超时，请刷新")
        except Exception as exc:
            log.exception("login failed")
            self._set("error", str(exc))


class ProfileService:
    def __init__(self, state: WebState):
        self.state = state
        self.cache = ProfileCache()
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None
        self.stop_event = threading.Event()
        self.generation = 0

    def refresh(self, settings: Settings, players: list[dict], force: bool = False) -> None:
        ids = []
        seen = set()
        for player in players:
            sid = str(player.get("sid") or "")
            if sid and sid not in seen:
                seen.add(sid)
                ids.append(sid)
        if not ids:
            return
        with self.lock:
            self.generation += 1
            generation = self.generation
            self.stop_event.set()
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=1)
            self.stop_event = threading.Event()
            self.thread = threading.Thread(
                target=self._run,
                args=(generation, settings, ids, force),
                name="pvp-profiles",
                daemon=True,
            )
            self.thread.start()

    def clear(self) -> None:
        self.cache.clear()

    def _run(self, generation: int, settings: Settings, ids: list[str], force: bool) -> None:
        token = settings.token
        my_sid = settings.account_steam_id or settings.steam_id
        stop = self.stop_event
        bases: dict[str, Profile] = {}

        def base_fetch(sid: str) -> Profile:
            cached = None if force else self.cache.get(sid)
            if cached and cached.score > 0:
                return cached
            profile = Profile(steam_id=sid)
            try:
                client = ApiClient()
                rows = client.search_user(sid)
                exact = next(
                    (r for r in rows if str(r.get("steamId") or "") == sid), None
                )
                if exact:
                    profile.apply_search(exact)
            except Exception as exc:
                log.debug("search failed %s: %s", sid, exc)
            return profile

        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(base_fetch, sid): sid for sid in ids}
                for future in as_completed(futures):
                    if stop.is_set() or generation != self.generation:
                        return
                    sid = futures[future]
                    try:
                        profile = future.result()
                    except Exception:
                        profile = Profile(steam_id=sid)
                    bases[sid] = profile
                    self.cache.put(profile)
                    self.state.set_profile(profile)
        except Exception:
            log.exception("profile base phase failed")

        if stop.is_set() or generation != self.generation or not token or not my_sid:
            return

        client = ApiClient()
        for sid in ids:
            if stop.is_set() or generation != self.generation:
                return
            profile = bases.get(sid) or self.cache.get(sid) or Profile(steam_id=sid)
            if (
                not force
                and profile.rating is not None
                and profile.peak_score > 0
                and profile.stars_known
            ):
                continue
            try:
                if profile.rating is None or force:
                    detail = client.detail_stats(token, my_sid, sid)
                    profile.apply_detail(detail)
                    self.cache.put(profile)
                    self.state.set_profile(profile)
            except AuthExpired as exc:
                self.state.set_auth_error(str(exc))
                return
            except ApiError as exc:
                log.debug("detail failed %s: %s", sid, exc)
            except Exception as exc:
                log.debug("detail error %s: %s", sid, exc)

            # detailStats 被限流/失败时，赛季接口仍能提供段位分、S 星数和场次。
            try:
                if profile.peak_score <= 0 or not profile.stars_known or force:
                    seasons = client.season_scores(token, sid)
                    profile.apply_seasons(seasons)
                    self.cache.put(profile)
                    self.state.set_profile(profile)
            except AuthExpired as exc:
                self.state.set_auth_error(str(exc))
                return
            except ApiError as exc:
                log.debug("season failed %s: %s", sid, exc)
            except Exception as exc:
                log.debug("season error %s: %s", sid, exc)
            # 连续查询 10 人时平台会返回 4013“无效的账号”，放慢并留出重试空间。
            time.sleep(0.35)
