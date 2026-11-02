"""静态安全审计：检查常见高风险行为是否出现在代码中。

注意：审计通过只能证明代码没有使用这些高风险 API，
不能、也不应该被理解为“100% 不会被封”。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
SCAN_DIRS = [ROOT / "pvp_web", ROOT / "pvp_insight"]

FORBIDDEN = {
    "OpenProcess": "打开游戏进程",
    "ReadProcessMemory": "读取游戏内存",
    "WriteProcessMemory": "写入游戏内存",
    "CreateRemoteThread": "远程线程注入",
    "SetWindowsHookEx": "全局钩子",
    "GetAsyncKeyState": "键盘状态监听",
    "GetKeyboardState": "键盘状态监听",
    "SendInput": "模拟键鼠输入",
    "keybd_event": "模拟键盘输入",
    "mouse_event": "模拟鼠标输入",
    "pynput": "键鼠监听库",
    "frida": "动态注入框架",
    "pymem": "游戏内存读写库",
    "cs2.exe": "游戏进程名",
    "Counter-Strike Global Offensive": "游戏安装目录",
    "\\Steam\\steamapps": "游戏安装目录",
}

ALLOWED_HOSTS = {
    "127.0.0.1",
    "localhost",
    "api.wmpvp.com",
    "pwaweblogin.wmpvp.com",
    "appengine.wmpvp.com",
    "appactivity.wmpvp.com",
    "passport.pwesports.cn",
    "news.wmpvp.com",
    "client.wmpvp.com",
    "img.wmpvp.com",
    "cdn.wmpvp.com",
    "steamcdn-a.akamaihd.net",
}

URL_RE = re.compile(r"https?://[^\s\"'<>)]+")
CTYPES_RE = re.compile(r"ctypes\.windll\.([A-Za-z0-9_]+)")


def python_files():
    for directory in SCAN_DIRS:
        if not directory.exists():
            continue
        for path in directory.rglob("*.py"):
            if path.name == "audit.py":
                continue
            yield path


def main() -> int:
    failures: list[str] = []
    warnings: list[str] = []
    scanned = 0
    for path in python_files():
        scanned += 1
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(f"{path}: 无法读取 ({exc})")
            continue
        relative = path.relative_to(ROOT)
        for token, label in FORBIDDEN.items():
            if token.lower() in text.lower():
                failures.append(f"{relative}: 发现 {token}（{label}）")
        for url in URL_RE.findall(text):
            try:
                host = urlparse(url).hostname or ""
            except ValueError:
                continue
            if host and host not in ALLOWED_HOSTS and not host.endswith(".wmpvp.com"):
                warnings.append(f"{relative}: 外部地址 {host}")

    server_file = ROOT / "pvp_web" / "server.py"
    if server_file.exists():
        server_text = server_file.read_text(encoding="utf-8")
        if '("127.0.0.1", port)' not in server_text:
            failures.append("pvp_web/server.py: 未确认仅绑定 127.0.0.1")
        if "0.0.0.0" in server_text:
            failures.append("pvp_web/server.py: 监听地址包含 0.0.0.0")
    else:
        failures.append("pvp_web/server.py 不存在")

    ui_file = ROOT / "pvp_web" / "static" / "index.html"
    if ui_file.exists():
        ui_text = ui_file.read_text(encoding="utf-8")
        if "steam" not in ui_text.lower():
            warnings.append("index.html: 未发现 Steam 覆盖层说明")

    print("=" * 62)
    print("  PVP Insight · 静态安全审计")
    print("=" * 62)
    print(f"扫描 Python 文件: {scanned} 个")
    print()
    print("[PASS] 未发现进程注入 / 内存读写 API" if not any(
        "OpenProcess" in item or "ReadProcessMemory" in item or "WriteProcessMemory" in item
        or "CreateRemoteThread" in item or "frida" in item or "pymem" in item
        for item in failures
    ) else "[FAIL] 发现进程注入 / 内存读写相关代码")
    print("[PASS] 未发现全局钩子 / 键鼠监听 / 输入模拟" if not any(
        "SetWindowsHookEx" in item or "GetAsyncKeyState" in item
        or "GetKeyboardState" in item or "SendInput" in item
        or "keybd_event" in item or "mouse_event" in item or "pynput" in item
        for item in failures
    ) else "[FAIL] 发现键鼠监听 / 输入模拟相关代码")
    print("[PASS] 未发现游戏进程名 / 游戏安装目录引用" if not any(
        "cs2.exe" in item or "Counter-Strike" in item or "steamapps" in item
        for item in failures
    ) else "[FAIL] 发现游戏进程或安装目录引用")
    print("[PASS] 服务仅监听 127.0.0.1" if not any(
        "0.0.0.0" in item or "未确认仅绑定" in item for item in failures
    ) else "[FAIL] 监听地址不是仅本机")
    print("[PASS] 网络目标仅完美平台公开接口" if not warnings else "[WARN] 发现非完美平台地址")

    if failures:
        print()
        print("失败项:")
        for item in failures:
            print("  -", item)
        print()
        print("审计未通过：请勿继续使用，先修复上述问题。")
        return 1

    print()
    print("结论：代码未包含常见的注入、内存读取、键鼠监听/模拟、")
    print("      游戏文件访问或对局域网开放监听。")
    print()
    print("注意：审计通过 ≠ 100% 不封。它只能证明本工具没有采用这些")
    print("      高风险技术；平台规则与人工判断仍然无法预测。")
    print("=" * 62)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
