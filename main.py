# -*- coding: utf-8 -*-
"""东方神速（East Speed）启动入口。

用法:
    python main.py              启动图形界面
    python main.py --selftest   打包后自检（不弹窗）：校验关键依赖与资源，
                                成功返回退出码 0，失败返回 1；
                                GUI 版另在 exe 同级写 selftest_diag.txt
"""
import sys


def selftest() -> int:
    """frozen/源码环境自检：导入关键模块、校验品牌资源与扩展释放。"""
    diag_lines = []

    def log(msg):
        print(msg)
        diag_lines.append(str(msg))

    code = 1
    try:
        import os
        import traceback
        from core import branding, app_paths  # noqa: F401
        from core import (  # noqa: F401
            engine, task, hls, ftp_task, http2, torrent, grabber,
            checksum, prealloc, antivirus, bridge,
        )
        from core.branding import app_icon_path
        from core.app_paths import (
            app_dir, bundled_extension_dir, deployed_extension_dir,
            ensure_browser_extension, is_frozen, resource_root,
        )

        log(f"frozen={is_frozen()} _MEIPASS={getattr(sys, '_MEIPASS', None)}")
        log(f"app_dir={app_dir()} resource_root={resource_root()}")
        log(f"bundled={bundled_extension_dir()} "
            f"exists={os.path.isdir(bundled_extension_dir())}")
        log(f"deployed(before)={deployed_extension_dir()} "
            f"exists={os.path.isdir(deployed_extension_dir())}")

        bt = bool(torrent.libtorrent_available())
        try:
            import h2  # noqa: F401
            h2_ok = True
        except Exception:
            h2_ok = False
        try:
            import requests_ntlm  # noqa: F401
            import spnego  # noqa: F401
            import cryptography  # noqa: F401
            ntlm_ok = True
        except Exception:
            ntlm_ok = False
        try:
            from core import proxy_support, updater  # noqa: F401
            updater_ok = True
        except Exception:
            updater_ok = False
        try:
            import truststore  # noqa: F401
            truststore_ok = True
        except Exception:
            truststore_ok = False

        icon_ok = os.path.isfile(app_icon_path())

        ext_dir = None
        try:
            ext_dir = ensure_browser_extension()
        except Exception:
            log("ensure error:\n" + traceback.format_exc())
        ext_ok = bool(ext_dir) and os.path.isfile(
            os.path.join(ext_dir, "manifest.json"))
        log(f"ensure returned={ext_dir} ext_ok={ext_ok}")
        log(f"deployed(after) exists={os.path.isdir(deployed_extension_dir())}")
        log(f"app={branding.APP_NAME} v{branding.APP_VERSION} "
            f"BT={bt} HTTP2={h2_ok} NTLM={ntlm_ok} UPDATER={updater_ok} "
            f"TRUSTSTORE={truststore_ok} icon={icon_ok}")

        ok = icon_ok and ext_ok
        log("RESULT " + ("PASS" if ok else "FAIL"))
        code = 0 if ok else 1
    except Exception:
        import traceback
        log("FATAL\n" + traceback.format_exc())
        code = 1

    # GUI subsystem 下 stdout 不可见，把诊断写到临时目录（安装目录可能只读）
    try:
        import os
        import tempfile
        diag_path = os.path.join(tempfile.gettempdir(),
                                 "DongFangSpeed_selftest_diag.txt")
        with open(diag_path, "w", encoding="utf-8") as f:
            f.write("\n".join(diag_lines))
    except Exception:
        pass
    return code


def warm_imports() -> None:
    """在主线程、启动 GUI 前预先导入可选/原生模块。

    PyInstaller 单文件版若在多个工作线程中“首次导入”带原生扩展的模块，
    可能与 bootloader 解压/导入锁竞争导致卡死或崩溃；启动时单线程预热可规避。
    """
    for name in (
        "truststore", "certifi", "httpx", "h2", "hpack", "hyperframe",
        "libtorrent", "requests_ntlm", "spnego", "cryptography", "cffi",
    ):
        try:
            __import__(name)
        except Exception:  # noqa: BLE001  可选依赖缺失属正常
            pass


def install_crash_hooks() -> None:
    """把主线程/工作线程未捕获异常写入 crash.log，避免静默闪退且便于定位。"""
    import datetime
    import os
    import threading
    import traceback

    try:
        from core.app_paths import app_data_dir
        log_dir = app_data_dir()
        os.makedirs(log_dir, exist_ok=True)
        log_path = os.path.join(log_dir, "crash.log")
    except Exception:  # noqa: BLE001
        log_path = os.path.join(os.path.expanduser("~"),
                                "DongFangSpeed_crash.log")

    def _write(title: str, text: str) -> None:
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n==== %s  %s ====\n%s\n" % (
                    title, datetime.datetime.now().isoformat(), text))
        except Exception:  # noqa: BLE001
            pass

    def _excepthook(exc_type, exc, tb):
        _write("主线程未捕获异常",
               "".join(traceback.format_exception(exc_type, exc, tb)))
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:  # noqa: BLE001
            pass

    def _thread_hook(args):
        _write("工作线程未捕获异常 [%s]" % getattr(args.thread, "name", "?"),
               "".join(traceback.format_exception(
                   args.exc_type, args.exc_value, args.exc_traceback)))

    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    warm_imports()
    install_crash_hooks()
    from gui.app import run
    sys.exit(run())
