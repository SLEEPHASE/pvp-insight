"""网页版演示数据（--demo）。"""

from __future__ import annotations

from pvp_insight.api import Profile

DEMO_PLAYERS = [
    ("76561198000000001", "CT", 21, 14, 6, 92.4),
    ("76561198000000002", "CT", 17, 15, 4, 81.2),
    ("76561198000000003", "CT", 14, 16, 3, 74.8),
    ("76561198000000004", "CT", 12, 18, 5, 66.1),
    ("76561198000000005", "CT", 9, 19, 2, 55.3),
    ("76561198000000006", "T", 24, 12, 7, 101.6),
    ("76561198000000007", "T", 19, 13, 5, 88.9),
    ("76561198000000008", "T", 16, 15, 4, 79.4),
    ("76561198000000009", "T", 13, 17, 3, 70.2),
    ("76561198000000010", "T", 8, 20, 1, 48.7),
]

NAMES = ["Astra", "Nova", "Vex", "Kite", "Echo", "Rift", "Onyx", "Pulse", "Wraith", "Zero"]

AVATARS = [
    "https://img.wmpvp.com/pvp/fc/05/fc05233820985ab22d9219bd5348c8871793536142.webp",
    "https://cdn.wmpvp.com/avatars/e1da84647b32aa30868f4770d5b8da806316cca4.jpg",
    "https://img.wmpvp.com/pvp/9e/21/9e21e239eedd6a2694f8cdacbbf92b8e1674411694.jpg",
    "https://cdn.wmpvp.com/avatars/290bdd165c91ba40829293f29906cb7cc240a508.jpg",
    "https://cdn.wmpvp.com/avatars/8a49c3f03cbb0da8c0dfac6b4cc7e1c2eaa06e94.jpg",
    "https://cdn.wmpvp.com/avatars/437234a5d3708de1d47207922ce08de01be2effd.jpg",
    "https://cdn.wmpvp.com/avatars/d79bd1c4b56752aab0c4c9cf5f425d585a9d80ee.jpg",
    "https://cdn.wmpvp.com/avatars/fef49e7fa7e1997310d705b2a6158ff8dc1cdfeb.jpg",
    "https://img.wmpvp.com/pvp/77/94/7794dd2d2ab0a1db737a73d76f71495a1733558328.png",
    "https://cdn.wmpvp.com/avatars/290bdd165c91ba40829293f29906cb7cc240a508.jpg",
]

PROFILE_DATA = [
    (2401, 3, 1.34, 412, 58.2, 1.42, 2550, 6, "S3"),
    (2280, 0, 1.22, 356, 55.1, 1.28, 2310, 2, "S2"),
    (2160, 0, 1.15, 298, 52.4, 1.19, 2205, 0, "S1"),
    (1980, 0, 1.06, 264, 50.8, 1.05, 2040, 0, "S3"),
    (1840, 0, 0.96, 197, 47.2, 0.94, 1900, 0, "S2"),
    (2350, 1, 1.29, 388, 56.7, 1.35, 2420, 4, "S3"),
    (2210, 0, 1.18, 331, 53.9, 1.21, 2260, 1, "S1"),
    (2070, 0, 1.10, 287, 51.3, 1.12, 2110, 0, "S2"),
    (1930, 0, 1.01, 243, 49.4, 1.00, 1990, 0, "S3"),
    (1770, 0, 0.92, 188, 46.0, 0.89, 1820, 0, "S1"),
]


def demo_match() -> dict:
    rounds = 25
    players = []
    for sid, side, kills, deaths, assists, adr in DEMO_PLAYERS:
        rt = (
            0.4 * kills / rounds
            + 0.1 * assists / rounds
            + 0.006 * adr
            - 0.3 * deaths / rounds
            + 0.3
        )
        players.append(
            {
                "sid": sid,
                "side": side,
                "kills": kills,
                "deaths": deaths,
                "assists": assists,
                "adr": adr,
                "headshot": 0,
                "alive": True,
                "rt": round(rt, 2),
            }
        )
    return {
        "id": "PVP@DEMO20261103",
        "map_raw": "de_mirage",
        "map": "荒漠迷城",
        "ct_score": 13,
        "t_score": 11,
        "ct_half": 7,
        "t_half": 5,
        "round": 25,
        "players": players,
        "start_time": "",
        "received_at": 0,
    }


def demo_profiles() -> dict[str, Profile]:
    result: dict[str, Profile] = {}
    for index, (sid, _side, *_rest) in enumerate(DEMO_PLAYERS):
        score, stars, rating, matches, win, kd, peak, peak_stars, season = PROFILE_DATA[index]
        result[sid] = Profile(
            steam_id=sid,
            name=NAMES[index],
            avatar=AVATARS[index] if index < len(AVATARS) else "",
            score=score,
            stars=stars if score > 2400 else 0,
            stars_known=score > 2400,
            rating=rating,
            matches=matches,
            win_rate=win,
            kd=kd,
            adr=82.0,
            peak_score=peak,
            peak_stars=peak_stars,
            peak_season=season,
            season="S3",
            source="demo",
        )
    return result
