# -*- coding: utf-8 -*-
"""「东方神速」Windows 打包脚本（PyInstaller）。

在装好依赖的 Python 环境运行（推荐 Python 3.11/3.12，以便内置 BT/磁力）：

    python tools/build_exe.py portable   # 便携版（单目录，开箱即用，启动快）
    python tools/build_exe.py single     # 单文件版（一个 exe，首次启动稍慢）
    python tools/build_exe.py both       # 两者都打（默认）

产物：
    dist/DongFangSpeed/DongFangSpeed.exe   便携目录（含浏览器扩展文件夹）
    dist/DongFangSpeed.exe                 单文件程序
"""
from __future__ import annotations

import datetime
import os
import re
import shutil
import sys
from pathlib import Path

# CI 的 Windows runner 默认控制台是英文代码页（cp1252），脚本里 print 中文
# （如“便携版”）会抛 UnicodeEncodeError 直接退出。强制标准流为 UTF-8，
# 对本机中文环境与 CI 英文环境都安全；errors="replace" 保证任何情况下不因输出崩溃。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "DongFangSpeed"
SEP = ";" if os.name == "nt" else ":"


def _module_available(mod: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(mod) is not None


def common_args(work: str) -> list[str]:
    gui_flag = "--console" if os.environ.get("PYD_CONSOLE") == "1" else "--windowed"
    args = [
        "--noconfirm", "--clean",
        gui_flag,                     # 正式为 --windowed；PYD_CONSOLE=1 时出控制台便于排错
        "--name", NAME,
        "--icon", os.path.join(ROOT, "assets", "app.ico"),
        # 随包数据：浏览器扩展 + 品牌资源（源用绝对路径，避免被 specpath 影响）
        "--add-data", f"{os.path.join(ROOT, 'browser_extension')}{SEP}browser_extension",
        "--add-data", f"{os.path.join(ROOT, 'assets')}{SEP}assets",
        # 测试与构建相关内容不打包
        "--exclude-module", "tests",
        "--exclude-module", "tools",
        "--exclude-module", "pyftpdlib",
        "--exclude-module", "hypercorn",
        "--exclude-module", "wsproto",
        "--exclude-module", "pytest",
        # 单实例命名管道（QLocalServer/QLocalSocket）依赖 QtNetwork
        "--hidden-import", "PyQt5.QtNetwork",
        # 开机自启 / 全部完成后电源动作（函数内 import，显式纳入）
        "--hidden-import", "core.autostart",
        "--hidden-import", "core.power",
        "--workpath", work,
        "--specpath", work,
        os.path.join(ROOT, "main.py"),
    ]
    # BT/磁力：libtorrent 是二进制扩展，整包收集（含其 plugins/dll）
    if _module_available("libtorrent"):
        args += ["--collect-all", "libtorrent"]
    else:
        print("[build] 提示：当前环境无 libtorrent，产物将不含 BT/磁力能力")
    # HTTP/2：收集 h2 系列纯 Python 子模块
    if _module_available("h2"):
        args += ["--collect-submodules", "h2",
                 "--hidden-import", "hpack",
                 "--hidden-import", "hyperframe"]
    # 代理 NTLM/Negotiate 认证：requests_ntlm/pyspnego 为纯 Python 子模块，
    # cryptography/cffi 含原生绑定，需整包收集，否则打包后导入失败
    for mod, mode in (("requests_ntlm", "sub"), ("spnego", "sub"),
                      ("cryptography", "all"), ("cffi", "all")):
        if _module_available(mod):
            args += (["--collect-all", mod] if mode == "all"
                     else ["--collect-submodules", mod])
    # truststore：让 requests 使用 Windows 系统证书库（信任企业内网 CA）
    if _module_available("truststore"):
        args += ["--collect-submodules", "truststore"]
    # 轻量化：排除用不到的 Qt 模块与重型标准库/测试库
    for _mod in PRUNE_EXCLUDES:
        args += ["--exclude-module", _mod]
    return args


# ---- 轻量化：用不到的 PyQt5/Qt 模块（命令行排除，onedir/onefile 均生效）----
PRUNE_EXCLUDES = [
    # Qt Quick / QML / 3D / WebEngine（本程序只用 QtWidgets）
    "PyQt5.QtQml", "PyQt5.QtQmlModels", "PyQt5.QtQmlWorkerScript",
    "PyQt5.QtQuick", "PyQt5.QtQuickWidgets", "PyQt5.QtQuick3D",
    "PyQt5.QtQuickControls2", "PyQt5.QtQuickTemplates2", "PyQt5.QtQuickLayouts",
    "PyQt5.QtOpenGL", "PyQt5.QtWebEngineCore", "PyQt5.QtWebEngineWidgets",
    "PyQt5.QtWebEngine", "PyQt5.QtWebEngineQuick", "PyQt5.QtWebChannel",
    "PyQt5.QtWebSockets", "PyQt5.QtMultimedia", "PyQt5.QtMultimediaWidgets",
    "PyQt5.QtMultimediaQuick", "PyQt5.QtPositioning", "PyQt5.QtLocation",
    "PyQt5.QtSensors", "PyQt5.QtBluetooth", "PyQt5.QtNfc",
    "PyQt5.QtSerialPort", "PyQt5.QtSerialBus", "PyQt5.QtSql", "PyQt5.QtTest",
    "PyQt5.QtDBus", "PyQt5.QtDesigner", "PyQt5.QtHelp", "PyQt5.QtPrintSupport",
    "PyQt5.QtSvg", "PyQt5.QtSvgWidgets", "PyQt5.QtCharts",
    "PyQt5.QtDataVisualization", "PyQt5.Qt3DCore", "PyQt5.Qt3DRender",
    "PyQt5.Qt3DInput", "PyQt5.Qt3DAnimation", "PyQt5.Qt3DExtras",
    "PyQt5.QtPdf", "PyQt5.QtPdfWidgets", "PyQt5.QtRemoteObjects",
    "PyQt5.QtTextToSpeech", "PyQt5.QtUiTools", "PyQt5.QtScxml",
    "PyQt5.QtHttpServer", "PyQt5.QtVirtualKeyboard", "PyQt5.QtGamepad",
    "PyQt5.QtSpatialAudio", "PyQt5.QtAxContainer", "PyQt5.QtNetworkAuth",
    "PyQt5.QtXml",
    # 用不到的标准库 / 重型第三方 / 测试库
    "tkinter", "_tkinter", "Tkinter", "pydoc", "lib2to3",
    "numpy", "matplotlib", "scipy", "pandas", "IPython", "notebook",
]

# ---- 轻量化：onedir 构建后按文件名删除的原生库（小写子串匹配）----
# 说明：QtWidgets 在 Windows 走原生 GDI 渲染，不需要软件 OpenGL(sw)/ANGLE/GLES、
# QML/Quick、多媒体、WebEngine 等；QtNetwork 仅用于本地命名管道（QLocalServer），
# 不做 TLS，故 Qt 自带的 OpenSSL 1.1 也可删（Python 的 HTTPS 走 libcrypto-3，保留）。
PRUNE_DLL_PARTS = [
    "opengl32sw", "d3dcompiler", "libglesv2", "libegl",
    "qt5quick", "qt5qml", "qt5opengl", "qt5webengine", "qtwebengine",
    "qt5webchannel", "qt5websockets", "qt5multimedia", "qt5positioning",
    "qt5location", "qt5sensors", "qt5bluetooth", "qt5nfc", "qt5serialport",
    "qt5serialbus", "qt5sql", "qt5test", "qt5dbus", "qt5designer", "qt5help",
    "qt5printsupport", "qt5svg", "qt5charts", "qt5datavisualization",
    "qt53d", "qt5pdf", "qt5remoteobjects", "qt5texttospeech", "qt5scxml",
    "qt5httpserver", "qt5virtualkeyboard", "qt5gamepad", "qt5spatialaudio",
    "qt5networkauth", "qt5xml", "libcrypto-1_1", "libssl-1_1", "icudtl",
]
# Qt 插件中用不到的子目录（相对 PyQt5/Qt5/plugins）
PRUNE_PLUGIN_DIRS = [
    "audio", "mediaservice", "playlistformats", "position", "bearer",
    "geoservices", "sensors", "sensorgestures", "webview", "renderplugins",
    "sceneparsing", "gamepads", "texttospeech", "nfc", "bluetooth",
    "sqldrivers", "printsupport", "designer", "tls", "canbus",
    "virtualkeyboard",
]


def _tree_size(path: str) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def prune_onedir(internal: str) -> int:
    """构建后裁剪 onedir 产物中用不到的 Qt 原生库/资源，返回释放字节数。"""
    removed = 0

    def rm_file(p: Path):
        nonlocal removed
        try:
            sz = p.stat().st_size
            p.unlink()
            removed += sz
        except OSError:
            pass

    def rm_tree(p: Path):
        nonlocal removed
        if p.is_dir():
            removed += _tree_size(str(p))
            shutil.rmtree(p, ignore_errors=True)

    # 1) 原生库黑名单（全目录扫描）
    for f in Path(internal).rglob("*"):
        if f.is_file() and any(k in f.name.lower() for k in PRUNE_DLL_PARTS):
            rm_file(f)

    qt = Path(internal) / "PyQt5" / "Qt5"
    # 2) QML 运行时目录
    rm_tree(qt / "qml")
    # 3) Qt 翻译仅保留中文
    tr = qt / "translations"
    if tr.is_dir():
        for f in tr.glob("*.qm"):
            if "zh" not in f.name.lower():
                rm_file(f)
    # 4) 不需要的 Qt 插件目录；imageformats 仅保留 ico（窗口图标）
    plugins = qt / "plugins"
    if plugins.is_dir():
        for d in PRUNE_PLUGIN_DIRS:
            rm_tree(plugins / d)
        imf = plugins / "imageformats"
        if imf.is_dir():
            for f in imf.glob("*.dll"):
                if f.name.lower() != "qico.dll":
                    rm_file(f)
        ie = plugins / "iconengines"
        if ie.is_dir():
            for f in ie.glob("*svg*"):
                rm_file(f)
    return removed


def maybe_sign():
    """构建后若提供了代码签名证书（环境变量 DFS_PFX），自动签名 dist 产物。"""
    pfx = os.environ.get("DFS_PFX", "")
    if not (pfx and os.path.isfile(pfx)):
        return
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    try:
        import sign_exe
    except Exception as exc:  # noqa: BLE001
        print(f"[build] 签名模块不可用，跳过：{exc}")
        return
    tool = sign_exe.find_signtool()
    if not tool:
        print("[build] 未找到 signtool.exe，跳过签名")
        return
    pwd = os.environ.get("DFS_PFX_PASSWORD", "")
    for target in sign_exe.default_targets():
        sign_exe.sign_file(target, pfx, pwd, tool)


def _run_pyinstaller(args: list[str]):
    import PyInstaller.__main__
    old = os.getcwd()
    os.chdir(ROOT)
    try:
        PyInstaller.__main__.run(args)
    finally:
        os.chdir(old)


def build_portable():
    print("[build] === 便携版（onedir）===")
    out = os.path.join(ROOT, "dist", NAME)
    work = os.path.join(ROOT, "build", "onedir")
    # 全新检出（如 CI）时 build/dist 尚不存在，PyInstaller 不会自建 specpath 多级目录
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    version_file = write_version_info()
    extra = ["--version-file", version_file] if version_file else []
    _run_pyinstaller(common_args(work)
                     + ["--onedir", "--distpath", os.path.join(ROOT, "dist")]
                     + extra)
    # 轻量化：裁剪用不到的 Qt 原生库/资源
    internal = os.path.join(out, "_internal")
    if os.path.isdir(internal):
        before = _tree_size(out)
        freed = prune_onedir(internal)
        after = _tree_size(out)
        print(f"[build] 瘦身：释放 {freed / 1048576:.1f} MB，"
              f"便携版 {before / 1048576:.1f} -> {after / 1048576:.1f} MB")
    # 便携目录顶层直接放一份浏览器扩展，用户无需先启动即可加载
    ext_dst = os.path.join(out, "browser_extension")
    if os.path.isdir(out) and not os.path.isdir(ext_dst):
        shutil.copytree(os.path.join(ROOT, "browser_extension"), ext_dst)
    for doc in ("README.md", "LICENSE"):
        src = os.path.join(ROOT, doc)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(out, doc))
    print("[build] 便携版目录：", out)


# ---- 单文件版专用 spec：在 Analysis 阶段过滤原生库（onefile 无法构建后裁剪）----
SINGLE_SPEC_TEMPLATE = r'''# -*- mode: python ; coding: utf-8 -*-
# 由 tools/build_exe.py 自动生成，请勿手改
import os
from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = {root!r}
EXCLUDES = {excludes}
DROP_DLL = {drop_dll}
DROP_PLUGIN_DIRS = {drop_plugin_dirs}

datas = [(os.path.join(ROOT, "browser_extension"), "browser_extension"),
         (os.path.join(ROOT, "assets"), "assets")]
binaries = []
hidden = ["PyQt5.QtNetwork", "core.autostart", "core.power"]
for pkg in ["libtorrent", "cryptography", "cffi"]:
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hidden += h
    except Exception:
        pass
for pkg in ["spnego", "requests_ntlm", "truststore", "h2", "hpack", "hyperframe"]:
    try:
        hidden += collect_submodules(pkg)
    except Exception:
        pass
for m in ["h2", "hpack", "hyperframe", "truststore", "spnego", "requests_ntlm"]:
    if m not in hidden:
        hidden.append(m)

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)


def _keep(item):
    name = item[0].replace("\\", "/").lower()
    base = name.rsplit("/", 1)[-1]
    # WebEngine 完全不用，相关二进制/资源一律剔除
    if "webengine" in name:
        return False
    if any(k in base for k in DROP_DLL):
        return False
    if "pyqt5/qt5/qml/" in name:
        return False
    if "pyqt5/qt5/libexec/" in name:
        return False
    if "pyqt5/qt5/translations/" in name and name.endswith(".qm"):
        if "zh" not in base:
            return False
    if "pyqt5/qt5/plugins/" in name:
        parts = name.split("/")
        if any(p in DROP_PLUGIN_DIRS for p in parts):
            return False
        if "/plugins/imageformats/" in name and base != "qico.dll":
            return False
        if "/plugins/iconengines/" in name and "svg" in base:
            return False
    return True


a.binaries = [x for x in a.binaries if _keep(x)]
a.datas = [x for x in a.datas if _keep(x)]

pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name={name!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    icon={icon!r},
    version={version},
    uac_admin=False,
)
'''


# ---- Windows 文件属性元数据（SignPath 要求设置并强制产品名/版本一致）----
VERSION_INFO_TEMPLATE = r'''# -*- coding: utf-8 -*-
# 由 tools/build_exe.py 依据 core/branding.py 自动生成，请勿手改
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={ver_tuple},
    prodvers={ver_tuple},
    mask=0x3F,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable(
        u'040904B0',
        [StringStruct(u'CompanyName', u'{name_en} Open Source'),
         StringStruct(u'FileDescription', u'{name_en} Download Manager'),
         StringStruct(u'FileVersion', u'{version}'),
         StringStruct(u'InternalName', u'{exe}'),
         StringStruct(u'LegalCopyright', u'Copyright (C) {year} {name_en} contributors'),
         StringStruct(u'LegalTrademarks', u'{name_en}'),
         StringStruct(u'OriginalFilename', u'{exe}.exe'),
         StringStruct(u'ProductName', u'{name_en}'),
         StringStruct(u'ProductVersion', u'{version}')]),
    ]),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
'''


def write_version_info() -> str | None:
    """按 core/branding.py 生成 Windows 版本元数据文件，返回路径（失败返回 None）。"""
    try:
        sys.path.insert(0, ROOT)
        from core.branding import APP_NAME_EN, EXE_NAME, APP_VERSION
    except Exception as exc:  # noqa: BLE001
        print(f"[build] 无法读取品牌信息，跳过版本元数据：{exc}")
        return None
    nums = [int(x) for x in re.findall(r"\d+", APP_VERSION)]
    nums = (nums + [0, 0, 0, 0])[:4]
    text = VERSION_INFO_TEMPLATE.format(
        ver_tuple=tuple(nums),
        version=APP_VERSION,
        name_en=APP_NAME_EN,
        exe=EXE_NAME,
        year=datetime.datetime.now().year,
    )
    os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
    path = os.path.join(ROOT, "build", "version_info.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def write_single_spec(version_file: str | None = None) -> str:
    os.makedirs(os.path.join(ROOT, "build"), exist_ok=True)
    spec_path = os.path.join(ROOT, "build", "dongfangspeed_single.spec")
    version_arg = repr(version_file) if version_file else "None"
    text = SINGLE_SPEC_TEMPLATE.format(
        root=ROOT,
        name=NAME,
        icon=os.path.join(ROOT, "assets", "app.ico"),
        version=version_arg,
        excludes=repr(PRUNE_EXCLUDES),
        drop_dll=repr(PRUNE_DLL_PARTS),
        drop_plugin_dirs=repr(PRUNE_PLUGIN_DIRS),
    )
    with open(spec_path, "w", encoding="utf-8") as f:
        f.write(text)
    return spec_path


def build_single():
    print("[build] === 单文件版（onefile，spec 过滤原生库）===")
    version_file = write_version_info()
    spec_path = write_single_spec(version_file)
    work = os.path.join(ROOT, "build", "onefile")
    os.makedirs(work, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    _run_pyinstaller([spec_path, "--noconfirm",
                      "--distpath", os.path.join(ROOT, "dist"),
                      "--workpath", work])
    exe = os.path.join(ROOT, "dist", NAME + ".exe")
    print(f"[build] 单文件： {exe}")
    print(f"[build] 大小： {os.path.getsize(exe) / 1048576:.1f} MB")


def main() -> int:
    mode = (sys.argv[1] if len(sys.argv) > 1 else "both").lower()
    if mode == "portable":
        build_portable()
    elif mode == "single":
        build_single()
    elif mode == "both":
        build_portable()
        build_single()
    else:
        print(__doc__)
        return 2
    maybe_sign()
    print("[build] 完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
