"""网页版共享状态与 JSON 序列化。"""

from __future__ import annotations

import threading
import time
from typing import Any

from pvp_insight.api import Profile
from pvp_insight.config import Settings
from pvp_insight.ranks import rank_for


def _rating(profile: Profile | None) -> float | None:
    return profile.rating if profile and profile.rating is not None else None


def profile_json(profile: Profile | None, sid: str = "") -> dict:
    if profile is None:
        return {
            "sid": sid,
            "name": "",
            "avatar": "",
            "score": 0,
            "stars": 0,
            "stars_known": False,
            "rank": "—",
            "rating": None,
            "matches": 0,
            "win_rate": None,
            "kd": None,
            "peak": "—",
            "peak_score": 0,
            "loaded": False,
        }
    return {
        "sid": profile.steam_id,
        "name": profile.name,
        "avatar": profile.avatar,
        "score": profile.score,
        "stars": profile.stars,
        "stars_known": profile.stars_known,
        "rank": profile.rank_text,
        "rating": profile.rating,
        "matches": profile.matches,
        "win_rate": profile.win_rate,
        "kd": profile.kd,
        "peak": profile.peak_text,
        "peak_score": profile.peak_score,
        "loaded": bool(profile.name or profile.score or profile.rating is not None),
    }


class WebState:
    def __init__(self, settings: Settings, demo: bool = False):
        self.settings = settings
        self.demo = demo
        self.lock = threading.RLock()
        self.match: dict | None = None
        self.profiles: dict[str, Profile] = {}
        self.status_state = "starting"
        self.status_text = "正在启动…"
        self.auth_error = ""
        self.last_update = time.time()
        self.match_ended = False
        self._status_at = 0.0
        self.revision = 0
        self._match_sig = None

    def touch(self) -> None:
        self.revision += 1
        self.last_update = time.time()

    @staticmethod
    def _match_signature(match: dict):
        players = match.get("players") or []
        rows = tuple(
            sorted(
                (
                    str(p.get("sid")),
                    str(p.get("side")),
                    p.get("kills"),
                    p.get("deaths"),
                    p.get("assists"),
                    round(float(p.get("adr") or 0), 1),
                )
                for p in players
            )
        )
        return (
            match.get("id"),
            match.get("ct_score"),
            match.get("t_score"),
            match.get("round"),
            rows,
        )

    # ------------------------------------------------------------------ 更新

    def set_status(self, state: str, text: str) -> None:
        with self.lock:
            if state == self.status_state and text == self.status_text:
                return
            self.status_state = state
            self.status_text = text
            self.touch()

    def set_match(self, match: dict) -> bool:
        with self.lock:
            signature = self._match_signature(match)
            if signature == self._match_sig and not self.match_ended:
                return False
            self._match_sig = signature
            old_id = (self.match or {}).get("id")
            self.match = match
            self.match_ended = False
            self.touch()
            return old_id != match.get("id")

    def set_ended(self) -> None:
        with self.lock:
            if not self.match or self.match_ended:
                return
            self.match_ended = True
            self.status_state = "ended"
            self.status_text = "对局已结束"
            self.touch()

    def clear_match(self) -> None:
        with self.lock:
            self.match = None
            self.match_ended = False
            self._match_sig = None
            self.profiles.clear()
            self.status_state = "waiting"
            self.status_text = "等待对局开始"
            self.touch()

    def set_profile(self, profile: Profile) -> None:
        with self.lock:
            old = self.profiles.get(profile.steam_id)
            if old is not None and old.__dict__ == profile.__dict__:
                return
            self.profiles[profile.steam_id] = profile
            self.touch()

    def get_profile(self, sid: str) -> Profile | None:
        with self.lock:
            return self.profiles.get(str(sid))

    def set_auth_error(self, message: str) -> None:
        with self.lock:
            if self.auth_error == message:
                return
            self.auth_error = message
            self.touch()

    def clear_auth_error(self) -> None:
        with self.lock:
            if not self.auth_error:
                return
            self.auth_error = ""
            self.touch()

    # ------------------------------------------------------------------ 查询

    def _own_side(self, players: list[dict]) -> str:
        sid = self.settings.steam_id
        if sid:
            for player in players:
                if player["sid"] == sid:
                    return player["side"]
        return ""

    def _sort_key(self, player: dict):
        profile = self.profiles.get(player["sid"])
        rating = profile.rating if profile and profile.rating is not None else -1
        score = profile.score if profile else 0
        return (-rating, -score, player["sid"])

    def _decorate(self, player: dict) -> dict:
        profile = self.profiles.get(player["sid"])
        item = dict(player)
        item.update(profile_json(profile, player["sid"]))
        item["side"] = player["side"]
        item["kills"] = player.get("kills", 0)
        item["deaths"] = player.get("deaths", 0)
        item["assists"] = player.get("assists", 0)
        item["adr"] = player.get("adr")
        item["rt"] = player.get("rt")
        item["alive"] = player.get("alive", False)
        return item

    def snapshot(self) -> dict:
        with self.lock:
            match = dict(self.match) if self.match else None
            players = list(match.get("players") or []) if match else []
            own_side = self._own_side(players)
            if own_side:
                opp_side = "T" if own_side == "CT" else "CT"
                own = [p for p in players if p["side"] == own_side]
                opp = [p for p in players if p["side"] != own_side]
                own_title, opp_title = "自己家", "对手"
            else:
                own_side, opp_side = "CT", "T"
                own = [p for p in players if p["side"] == "CT"]
                opp = [p for p in players if p["side"] == "T"]
                own_title, opp_title = "CT 方", "T 方"

            own.sort(key=self._sort_key)
            opp.sort(key=self._sort_key)
            own_json = [self._decorate(p) for p in own]
            opp_json = [self._decorate(p) for p in opp]

            own_ratings = [p["rating"] for p in own_json if p["rating"] is not None]
            opp_ratings = [p["rating"] for p in opp_json if p["rating"] is not None]
            own_scores = [p["score"] for p in own_json if p["score"] > 0]
            opp_scores = [p["score"] for p in opp_json if p["score"] > 0]
            own_avg = sum(own_ratings) / len(own_ratings) if own_ratings else None
            opp_avg = sum(opp_ratings) / len(opp_ratings) if opp_ratings else None
            if own_avg is not None and opp_avg is not None:
                pct = 100.0 / (1.0 + 10 ** ((own_avg - opp_avg) / 0.26))
            elif own_scores and opp_scores:
                score_diff = (
                    sum(own_scores) / len(own_scores)
                    - sum(opp_scores) / len(opp_scores)
                )
                pct = 100.0 / (1.0 + 10 ** (score_diff / 380.0))
            else:
                pct = 50.0
            pct = max(5.0, min(95.0, pct))

            return {
                "server_time": time.time(),
                "revision": self.revision,
                "status": {
                    "state": self.status_state,
                    "text": self.status_text,
                },
                "steam_id": self.settings.steam_id,
                "logged_in": bool(self.settings.token) or self.demo,
                "account": {
                    "nickname": self.settings.nickname if self.settings.nickname else ("DEMO" if self.demo else ""),
                    "avatar": self.settings.avatar,
                    "steam_id": self.settings.account_steam_id,
                },
                "auth_error": self.auth_error,
                "match": None
                if not match
                else {
                    "id": match.get("id", ""),
                    "map": match.get("map", ""),
                    "map_raw": match.get("map_raw", ""),
                    "ct_score": match.get("ct_score", 0),
                    "t_score": match.get("t_score", 0),
                    "ct_half": match.get("ct_half"),
                    "t_half": match.get("t_half"),
                    "round": match.get("round", 1),
                    "ended": self.match_ended,
                    "received_at": match.get("received_at", 0),
                },
                "teams": {
                    "own": {"title": own_title, "side": own_side, "players": own_json},
                    "opp": {"title": opp_title, "side": opp_side, "players": opp_json},
                },
                "summary": {
                    "own_rating": round(own_avg, 2) if own_avg is not None else None,
                    "opp_rating": round(opp_avg, 2) if opp_avg is not None else None,
                    "own_score": round(sum(own_scores) / len(own_scores))
                    if own_scores
                    else None,
                    "opp_score": round(sum(opp_scores) / len(opp_scores))
                    if opp_scores
                    else None,
                    "win_pct": round(pct, 1),
                },
            }
