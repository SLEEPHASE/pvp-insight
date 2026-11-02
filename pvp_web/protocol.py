"""完美平台实时对局数据解析（网页版，无 Qt 依赖）。"""

from __future__ import annotations

import time
from typing import Any

MAP_NAMES = {
    "de_dust2": "炙热沙城 II",
    "de_mirage": "荒漠迷城",
    "de_inferno": "炼狱小镇",
    "de_nuke": "核子危机",
    "de_overpass": "死亡游乐园",
    "de_ancient": "远古遗迹",
    "de_anubis": "阿努比斯",
    "de_vertigo": "殒命大厦",
    "de_train": "列车停放站",
    "de_cache": "Cache",
    "de_cbble": "古堡激战",
    "de_tuscan": "Tuscan",
    "cs_office": "办公室",
    "cs_italy": "意大利",
    "de_brewery": "酿酒厂",
    "de_grail": "圣杯",
    "de_warden": "Warden",
    "de_thera": "Thera",
    "de_poseidon": "Poseidon",
    "de_edin": "Edin",
    "de_palais": "Palais",
    "de_whistle": "Whistle",
    "de_jura": "Jura",
}


def map_name(raw: str) -> str:
    key = str(raw or "").strip().lower()
    if key in MAP_NAMES:
        return MAP_NAMES[key]
    if key.startswith("de_"):
        return key[3:].replace("_", " ").title()
    return key or "未知地图"


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in ("CT", "COUNTER-TERRORIST", "COUNTER_TERRORIST", "2"):
        return "CT"
    if text in ("T", "TERRORIST", "3", "1"):
        return "T"
    return text or "CT"


def normalize_payload(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    match_id = str(
        payload.get("matchId")
        or payload.get("match_id")
        or payload.get("id")
        or ""
    )
    raw_players = payload.get("playerList") or payload.get("players") or []
    if not isinstance(raw_players, list):
        raw_players = []

    ct_score = _int(payload.get("ctScore", payload.get("score1", 0)))
    t_score = _int(payload.get("terroristScore", payload.get("score2", 0)))
    rounds = max(1, ct_score + t_score + 1)

    players: list[dict] = []
    seen: set[str] = set()
    for row in raw_players:
        if not isinstance(row, dict):
            continue
        sid = str(
            row.get("steamId")
            or row.get("steam_id")
            or row.get("playerId")
            or row.get("uid")
            or ""
        ).strip()
        if not sid.isdigit() or sid in seen:
            continue
        seen.add(sid)
        kills = _int(row.get("kill", row.get("kills", 0)))
        deaths = _int(row.get("death", row.get("deaths", row.get("deathCount", 0))))
        assists = _int(row.get("assist", row.get("assists", 0)))
        adr = row.get("adr")
        try:
            adr_value = float(adr) if adr not in (None, "") else None
        except (TypeError, ValueError):
            adr_value = None

        rt = (
            0.4 * (kills / rounds)
            + 0.1 * (assists / rounds)
            + 0.006 * (adr_value or 0.0)
            - 0.3 * (deaths / rounds)
            + 0.3
        )
        rt = max(0.0, min(3.0, rt))

        players.append(
            {
                "sid": sid,
                "side": _side(row.get("side") or row.get("team") or row.get("camp")),
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "adr": adr_value,
                "headshot": _int(row.get("headshot", 0)),
                "alive": bool(row.get("alive")),
                "rt": round(rt, 2),
            }
        )

    if not match_id and not players:
        return None

    return {
        "id": match_id,
        "map_raw": str(payload.get("map") or payload.get("mapName") or ""),
        "map": map_name(str(payload.get("map") or payload.get("mapName") or "")),
        "ct_score": ct_score,
        "t_score": t_score,
        "ct_half": payload.get("ctHalfScore"),
        "t_half": payload.get("terroristHalfScore"),
        "round": rounds,
        "players": players,
        "start_time": payload.get("startTime") or "",
        "received_at": time.time(),
    }
