"""PyInstaller 打包入口（也用于源码方式启动）。"""

from pvp_web.server import main

if __name__ == "__main__":
    raise SystemExit(main())
