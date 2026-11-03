"""完美世界竞技平台公开接口封装（非官方）。

包含：
- 玩家搜索 / 实时对局 WebSocket 地址（无需登录）
- 玩家赛季数据 / 历史赛季段位（需要扫码登录令牌）
- 扫码登录
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests

from .config import write_debug_json
from .ranks import rank_for

log = logging.getLogger(__name__)

APP_VERSION = "3.5.4.172"
API_HOST = "https://api.wmpvp.com"
LOGIN_HOST = "https://pwaweblogin.wmpvp.com"
APPENGINE_HOST = "https://appengine.wmpvp.com"
ACTIVITY_HOST = "https://appactivity.wmpvp.com"
PASSPORT_HOST = "https://passport.pwesports.cn"

# 平台客户端公开使用的前端签名盐（用于实时观战接口）
LIVE_SECURITY_KEY = "b2K%$5k*o^j!@Qp"

_MOBILE_UA = "okhttp/4.11.0"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_JSON_HEADERS = {
    "User-Agent": _MOBILE_UA,
    "appversion": APP_VERSION,
    "platform": "android",
    "Accept": "application/json",
    "Content-Type": "application/json;charset=UTF-8",
}


class ApiError(RuntimeError):
    pass


class AuthExpired(ApiError):
    pass


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            text = value.strip()
            # SteamID64 是 17 位整数，绝不能先转 float：
            # float 精度只有约 15-16 位，会悄悄改掉最后一位。
            if text.isdigit() or (
                text[:1] in ("+", "-") and text[1:].isdigit()
            ):
                return int(text)
        return int(round(float(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def _as_float(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        text = str(value).strip().rstrip("%")
        number = float(text)
        return number
    except (TypeError, ValueError):
        return default


def _first(data: dict, *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _normalize_pct(value: Any) -> float | None:
    number = _as_float(value)
    if number is None:
        return None
    if 0 < number <= 1.0001:
        number *= 100.0
    if number < 0:
        return None
    return round(min(number, 100.0), 1)


@dataclass
class Profile:
    steam_id: str
    name: str = ""
    avatar: str = ""
    score: int = 0
    stars: int = 0
    stars_known: bool = False
    rating: float | None = None
    matches: int = 0
    win_rate: float | None = None
    kd: float | None = None
    adr: float | None = None
    rws: float | None = None
    hs: float | None = None
    mvp: int = 0
    peak_score: int = 0
    peak_stars: int = 0
    peak_season: str = ""
    season: str = ""
    source: str = ""
    updated_at: float = field(default_factory=time.time)

    @property
    def rank(self):
        return rank_for(self.score, self.stars)

    @property
    def rank_text(self) -> str:
        rank = self.rank
        if rank.label == "S":
            if self.stars_known:
                return f"S ★{self.stars}"
            return "S"
        return rank.label

    @property
    def peak_rank(self):
        return rank_for(self.peak_score, self.peak_stars)

    @property
    def peak_text(self) -> str:
        if self.peak_score <= 0:
            return "—"
        if self.peak_rank.label == "S":
            text = f"S ★{self.peak_stars}"
        else:
            text = self.peak_rank.label
        if self.peak_season:
            text += f" · {self.peak_season}"
        return text

    @property
    def kd_text(self) -> str:
        return f"{self.kd:.2f}" if self.kd is not None else "—"

    @property
    def win_text(self) -> str:
        return f"{self.win_rate:.0f}%" if self.win_rate is not None else "—"

    def copy(self) -> "Profile":
        return Profile(**{**self.__dict__})

    def apply_search(self, row: dict) -> None:
        self.name = str(
            _first(row, "pvpNickName", "nickname", "name", "steamNickName") or self.name
        )
        self.avatar = str(
            _first(row, "pvpAvatar", "avatar", "steamAvatar") or self.avatar
        )
        score = _first(row, "pvpScore", "aveScore", "score")
        if score is not None:
            self.score = _as_int(score)
        self.source = "search"

    def apply_detail(self, data: dict) -> None:
        if not isinstance(data, dict):
            return
        self.name = str(_first(data, "name", "nickname", "pvpNickName") or self.name)
        self.avatar = str(_first(data, "avatar", "pvpAvatar") or self.avatar)

        score = _first(data, "pvpScore", "score", "aveScore")
        if score is not None:
            self.score = _as_int(score)
        stars = _first(data, "stars", "curr_s_stars", "pvpStars")
        if stars is not None:
            self.stars = _as_int(stars)
            self.stars_known = True

        rating = _as_float(_first(data, "pwRating", "commonRating", "rating", "avgRating"))
        if rating is not None and rating > 0:
            if rating > 5:
                rating = rating / 100.0
            self.rating = round(rating, 2)
        elif rating is not None and rating <= 0:
            self.rating = None

        matches = _first(data, "cnt", "matchCount", "totalMatch", "match_count")
        if matches is not None:
            self.matches = _as_int(matches)

        win_rate = _first(data, "winRate", "win_rate", "winningRate")
        self.win_rate = _normalize_pct(win_rate)

        kills = _as_int(_first(data, "kills", "kill"))
        deaths = _as_int(_first(data, "deaths", "death"))
        if kills > 0 and deaths > 0:
            self.kd = round(kills / deaths, 2)
        else:
            kd = _as_float(_first(data, "kd", "KD", "killDeathRatio"))
            if kd is not None:
                self.kd = round(kd, 2)

        adr = _as_float(_first(data, "adr", "ADR", "avgAdr"))
        if adr is not None:
            self.adr = round(adr, 1)
        rws = _as_float(_first(data, "rws", "RWS", "avgRws"))
        if rws is not None:
            self.rws = round(rws, 1)
        hs = _normalize_pct(_first(data, "headShotRatio", "hsRate", "headshotRate"))
        if hs is not None:
            self.hs = round(hs, 1)
        mvp = _first(data, "mvpCount", "mvp")
        if mvp is not None:
            self.mvp = _as_int(mvp)

        season = _first(data, "seasonId", "season", "csgoSeasonId")
        if season:
            self.season = str(season)

        peak = _first(
            data,
            "maxPvpScore",
            "maxScore",
            "highestScore",
            "peakScore",
            "historyMaxScore",
            "maxPvpRank",
        )
        if peak is not None:
            peak_value = _as_int(peak)
            if peak_value > 0:
                self.peak_score = peak_value

        self.updated_at = time.time()
        if self.source != "search":
            self.source = "detail"

    def apply_seasons(self, rows: list[dict]) -> None:
        best: tuple[tuple[int, int], str, int] | None = None
        latest: dict | None = None
        latest_order = -1
        for row in rows:
            if not isinstance(row, dict):
                continue
            score = _as_int(_first(row, "score", "pvpScore", "aveScore"))
            if score <= 0:
                continue
            stars = _as_int(_first(row, "curr_s_stars", "stars"))
            season = str(_first(row, "season", "seasonId", "name") or "")
            key = (score, stars)
            if best is None or key > best[0]:
                best = (key, season, stars)
            digits = "".join(ch for ch in season if ch.isdigit())
            order = int(digits) if digits else 0
            if latest is None or order > latest_order:
                latest = row
                latest_order = order
        if latest is not None:
            # detailStats 失败时，用最新赛季兜底：段位分、S 星数、场次。
            if not self.stars_known or self.score <= 0:
                self.score = _as_int(
                    _first(latest, "score", "pvpScore", "aveScore")
                )
                self.stars = _as_int(
                    _first(latest, "curr_s_stars", "stars")
                )
                self.stars_known = True
            if self.matches <= 0:
                self.matches = _as_int(
                    _first(latest, "match_count", "matchCount", "cnt")
                )
        if best is not None:
            (score, stars), season, _ = best
            if score >= self.peak_score:
                self.peak_score = score
                self.peak_stars = stars
                self.peak_season = season
        self.updated_at = time.time()


class ApiClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self._verify = True

    def _request(
        self,
        method: str,
        url: str,
        *,
        timeout: tuple[float, float] = (6.0, 18.0),
        **kwargs,
    ) -> requests.Response:
        kwargs.setdefault("timeout", timeout)
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return self.session.request(method, url, verify=self._verify, **kwargs)
            except requests.exceptions.SSLError as exc:
                # 少数系统证书链不完整，第二次关闭校验（仅本机只读请求）
                self._verify = False
                last_error = exc
            except requests.exceptions.RequestException as exc:
                last_error = exc
                if attempt == 0:
                    time.sleep(0.4)
        raise ApiError(f"网络请求失败：{last_error}")

    def _json(self, response: requests.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise ApiError(f"平台返回了非 JSON 数据（HTTP {response.status_code}）") from exc
        if not isinstance(data, dict):
            raise ApiError("平台返回数据格式异常")
        return data

    # ------------------------------------------------------------------ 公开接口

    def search_user(self, keyword: str) -> list[dict]:
        url = f"{APPENGINE_HOST}/steamcn/app/search/user"
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=UTF-8",
            "Origin": "https://news.wmpvp.com",
            "Referer": "https://client.wmpvp.com/",
            "x-requested-with": "XMLHttpRequest",
        }
        response = self._request(
            "POST",
            url,
            headers=headers,
            json={"keyword": str(keyword), "page": 1},
        )
        data = self._json(response)
        if _as_int(data.get("code"), -1) != 1:
            return []
        result = data.get("result")
        if isinstance(result, dict):
            result = result.get("list") or result.get("users") or []
        if not isinstance(result, list):
            return []
        return [row for row in result if isinstance(row, dict)]

    def get_ws_url(self, steam_id: str) -> str:
        stamp = hashlib.md5(
            f"steamId={steam_id}&securityKey={LIVE_SECURITY_KEY}".encode()
        ).hexdigest()
        url = (
            f"{ACTIVITY_HOST}/steamcn/match/watchStage/getWebsocketInfo"
            f"?steamId={steam_id}&platform=2&sign={stamp}"
        )
        headers = {
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json, text/plain, */*",
            "Referer": "https://client.wmpvp.com/",
        }
        response = self._request("GET", url, headers=headers)
        data = self._json(response)
        result = data.get("result") or {}
        ws_url = result.get("websocketUrl") if isinstance(result, dict) else None
        if _as_int(data.get("code"), -1) != 1 or not ws_url:
            raise ApiError("无法获取实时对局通道")
        return str(ws_url)

    # ------------------------------------------------------------------ 登录

    def qr_apply(self) -> tuple[str, str]:
        url = f"{PASSPORT_HOST}/qrAuth/applyToken"
        response = self._request(
            "POST",
            url,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json",
                "Content-Type": "application/json;charset=UTF-8",
            },
            json={"appId": "5", "qrType": "1", "redirect": False, "website": "pvp"},
        )
        data = self._json(response)
        result = data.get("result") or {}
        access_url = str(result.get("accessUrl") or "") if isinstance(result, dict) else ""
        if _as_int(data.get("code"), -1) != 0 or not access_url:
            raise ApiError("申请登录二维码失败，请稍后重试")
        token = ""
        try:
            token = (parse_qs(urlparse(access_url).query).get("accessToken") or [""])[0]
        except Exception:
            token = ""
        if not token and "accessToken=" in access_url:
            token = access_url.split("accessToken=")[1].split("&")[0]
        if not token:
            raise ApiError("登录二维码缺少令牌")
        return token, access_url

    def qr_check(self, access_token: str) -> dict:
        url = f"{PASSPORT_HOST}/qrAuth/check"
        response = self._request(
            "POST",
            url,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/json",
                "Content-Type": "application/json;charset=UTF-8",
            },
            json={"appId": "5", "accessToken": access_token},
        )
        data = self._json(response)
        result = data.get("result")
        return result if isinstance(result, dict) else {}

    # ------------------------------------------------------------------ 登录后接口

    def user_info(self, token: str, steam_id: str) -> dict:
        url = f"{LOGIN_HOST}/user-info"
        headers = {
            **_JSON_HEADERS,
            "token": token,
            "pwasteamid": steam_id,
            "Accept": "application/json, text/plain, */*",
        }
        response = self._request(
            "POST",
            url,
            headers=headers,
            data={
                "with_green_info": 1,
                "with_perfect_power": 1,
                "with_roles": 1,
                "with_ladder_info": "1",
                "access_token": token,
                "lang": "zh",
            },
        )
        data = self._json(response)
        code = _as_int(data.get("code"), -1)
        if code in (1002, 1006):
            raise AuthExpired("登录已失效")
        if code != 0:
            raise ApiError(str(data.get("msg") or "无法读取账号信息"))
        payload = data.get("data")
        if isinstance(payload, list) and payload:
            payload = payload[0]
        return payload if isinstance(payload, dict) else {}

    def detail_stats(self, token: str, my_steam_id: str, steam_id: str) -> dict:
        url = f"{API_HOST}/api/csgo/home/pvp/detailStats"
        headers = {**_JSON_HEADERS, "token": token}
        data: dict = {}
        for attempt in range(3):
            response = self._request(
                "POST",
                url,
                headers=headers,
                json={
                    "toSteamId": _as_int(steam_id),
                    "csgoSeasonId": "",
                    "mySteamId": _as_int(my_steam_id),
                    "accessToken": token,
                },
            )
            data = self._json(response)
            code = _as_int(data.get("statusCode"), -1)
            if code == 0:
                payload = data.get("data")
                if isinstance(payload, dict):
                    write_debug_json(f"detail_{steam_id}.json", data)
                    return payload
                raise ApiError("玩家数据为空")
            # 4013 在平台侧表现为“无效的账号”，连续查多人时常见，
            # 本质是频率限制；等待后重试。
            if code == 4013 and attempt < 2:
                time.sleep(1.2 + attempt * 0.8)
                continue
            if code in (4001, 401, 403, 1006):
                raise AuthExpired("登录已失效，请重新扫码")
            message = str(data.get("errorMessage") or "")
            if code == 90003:
                raise ApiError("平台拒绝了请求（请确认账号状态后重试）")
            raise ApiError(message or f"读取玩家数据失败（{code}）")
        raise ApiError("读取玩家数据失败")

    def season_scores(self, token: str, steam_id: str) -> list[dict]:
        url = f"{LOGIN_HOST}/user-info/season-ladder-score-list"
        headers = {
            **_JSON_HEADERS,
            "token": token,
            "pwasteamid": steam_id,
            "Accept": "application/json, text/plain, */*",
        }
        response = self._request(
            "GET",
            url,
            headers=headers,
            params={"uid": steam_id, "access_token": token},
        )
        data = self._json(response)
        code = _as_int(data.get("code"), -1)
        if code == 0:
            payload = data.get("data")
            if isinstance(payload, dict):
                payload = payload.get("data") or payload.get("list") or []
            if isinstance(payload, list):
                write_debug_json(f"seasons_{steam_id}.json", data)
                return [row for row in payload if isinstance(row, dict)]
            return []
        if code in (1002, 1006):
            raise AuthExpired("登录已失效，请重新扫码")
        raise ApiError(str(data.get("msg") or "无法读取历史赛季数据"))


def build_profile(
    steam_id: str,
    token: str,
    my_steam_id: str,
    client: ApiClient,
    *,
    with_detail: bool = True,
    with_seasons: bool = True,
    fallback_name: str = "",
) -> Profile:
    """读取单个玩家的公开段位与（登录后）赛季评分。"""
    profile = Profile(steam_id=str(steam_id), name=fallback_name)
    rows = client.search_user(steam_id)
    exact = next(
        (row for row in rows if str(row.get("steamId") or "") == str(steam_id)),
        None,
    )
    if exact:
        profile.apply_search(exact)
    elif rows:
        profile.apply_search(rows[0])

    if with_detail and token:
        detail = client.detail_stats(token, my_steam_id, steam_id)
        profile.apply_detail(detail)
    if with_seasons and token:
        seasons = client.season_scores(token, steam_id)
        profile.apply_seasons(seasons)
    return profile
