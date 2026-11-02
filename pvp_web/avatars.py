"""头像代理：带正确 Referer 拉取完美平台头像，本地缩放并缓存。

浏览器直接加载 CDN 头像时 Referer 是 127.0.0.1，会被部分 CDN 拒绝；
这里由服务端代为获取，同时把头像压到 128px，减少覆盖层解码开销。
"""

from __future__ import annotations

import io
import logging
import threading
import time
from urllib.parse import urlparse

import requests
from PIL import Image, ImageOps

from .state import WebState

log = logging.getLogger(__name__)

_ALLOWED_SUFFIXES = (".wmpvp.com", ".akamaihd.net")
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://client.wmpvp.com/",
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}


class AvatarService:
    def __init__(self, state: WebState, ttl: float = 21600.0):
        self.state = state
        self.ttl = ttl
        self.lock = threading.Lock()
        self.cache: dict[str, tuple[float, bytes, str]] = {}
        self.session = requests.Session()

    def get(self, sid: str) -> tuple[bytes, str] | None:
        profile = self.state.get_profile(sid)
        url = profile.avatar if profile else ""
        if not url:
            return None
        try:
            host = (urlparse(url).hostname or "").lower()
        except ValueError:
            return None
        if not host.endswith(_ALLOWED_SUFFIXES):
            return None

        with self.lock:
            item = self.cache.get(url)
            if item and time.time() - item[0] < self.ttl:
                return item[1], item[2]

        try:
            response = self.session.get(
                url, headers=_HEADERS, timeout=(5, 10), stream=True
            )
            if response.status_code != 200:
                log.debug("avatar HTTP %s: %s", response.status_code, host)
                return None
            raw = response.content
            content_type = response.headers.get("Content-Type", "image/jpeg")
            if not content_type.startswith("image/") or len(raw) > 8_000_000:
                return None
        except requests.RequestException as exc:
            log.debug("avatar fetch failed: %s", exc)
            return None

        processed = self._compress(raw)
        if processed is None:
            return None
        body, out_type = processed
        with self.lock:
            if len(self.cache) > 300:
                oldest = min(self.cache.items(), key=lambda item: item[1][0])[0]
                self.cache.pop(oldest, None)
            self.cache[url] = (time.time(), body, out_type)
        return body, out_type

    @staticmethod
    def _compress(raw: bytes) -> tuple[bytes, str] | None:
        try:
            image = Image.open(io.BytesIO(raw))
            image = ImageOps.exif_transpose(image)
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            image = ImageOps.fit(
                image,
                (128, 128),
                method=Image.Resampling.LANCZOS,
                centering=(0.5, 0.5),
            )
            buffer = io.BytesIO()
            image.save(buffer, format="WEBP", quality=82, method=4)
            return buffer.getvalue(), "image/webp"
        except Exception as exc:
            log.debug("avatar compress failed: %s", exc)
            return None
