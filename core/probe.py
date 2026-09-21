# -*- coding: utf-8 -*-
"""下载前探测：文件大小、是否支持 Range 断点续传、真实文件名。

最可靠的探测方式不是 HEAD，而是直接发一个 ``Range: bytes=0-0`` 的 GET：
- 返回 206 + Content-Range => 支持分段/续传；
- 返回 200 => 服务器忽略 Range，只能单线程整文件下载。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, unquote_to_bytes, urlparse

import requests

from . import tls
from .errors import DownloadError, ProbeError
from .proxy_support import apply_proxy_session, is_ntlm_proxy

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_RANGE_RE = re.compile(r"bytes\s+\d+-\d+/(?:(\d+)|\*)", re.IGNORECASE)


@dataclass
class FileInfo:
    url: str
    final_url: str = ""
    total_size: int | None = None
    resumable: bool = False
    filename: str = ""
    etag: str = ""
    last_modified: str = ""
    status_code: int = 0
    headers: dict = field(default_factory=dict)


# 常见 MIME 类型 -> 扩展名（头部与路径都给不出文件名时兜底，保证可打开/可分类）
_MIME_EXT_FALLBACK = {
    "application/zip": ".zip", "application/x-zip-compressed": ".zip",
    "application/x-rar-compressed": ".rar", "application/vnd.rar": ".rar",
    "application/x-7z-compressed": ".7z", "application/gzip": ".gz",
    "application/x-gzip": ".gz", "application/x-tar": ".tar",
    "application/pdf": ".pdf", "application/x-msdownload": ".exe",
    "application/x-msi": ".msi", "application/x-dosexec": ".exe",
    "application/vnd.android.package-archive": ".apk",
    "application/x-iso9660-image": ".iso", "application/epub+zip": ".epub",
    "application/json": ".json", "application/x-deb": ".deb",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "text/plain": ".txt", "text/html": ".html", "text/csv": ".csv",
    "video/mp4": ".mp4", "video/x-msvideo": ".avi", "video/quicktime": ".mov",
    "video/x-matroska": ".mkv", "video/webm": ".webm", "video/x-flv": ".flv",
    "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/wav": ".wav",
    "audio/x-wav": ".wav", "audio/flac": ".flac", "audio/ogg": ".ogg",
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
    "image/webp": ".webp", "image/svg+xml": ".svg", "image/x-icon": ".ico",
    "image/bmp": ".bmp",
}

_EXT_TOKEN_RE = re.compile(r"^[a-z0-9]{1,6}$", re.IGNORECASE)


def _has_proper_ext(name: str) -> bool:
    _, ext = os.path.splitext(os.path.basename(str(name)))
    return bool(ext) and bool(_EXT_TOKEN_RE.match(ext[1:]))


def _charset_candidates(charset: str):
    cs = (charset or "utf-8").lower().replace("_", "-")
    if cs in ("gb2312", "gbk", "gb18030", "zh-cn"):
        primary = "gb18030"
    elif cs in ("big5", "zh-tw", "zh-hk"):
        primary = "big5"
    else:
        primary = "utf-8"
    out = [primary]
    for c in ("utf-8", "gb18030"):
        if c not in out:
            out.append(c)
    return out


def _percent_decode(token: str, charset: str) -> str:
    """解码 RFC5987 的 percent-encoded 文件名（按其声明字符集）。"""
    try:
        raw = unquote_to_bytes(token)
    except Exception:  # noqa: BLE001
        return token
    for enc in _charset_candidates(charset):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _looks_like_text(u: str) -> bool:
    if not u or "\ufffd" in u:
        return False
    return not any(ord(ch) < 32 and ch != "\t" for ch in u)


def _repair_legacy_filename(token: str) -> str:
    """修复传统 filename= 中的非 ASCII 文件名。

    HTTP 头在 requests/urllib3 中按 latin-1 解码；中文服务器若直接塞入
    UTF-8 或 GBK 字节，这里先还原字节再依次尝试 UTF-8 / GB18030。
    """
    if all(ord(c) < 128 for c in token):
        return token
    raw = token.encode("latin-1", errors="replace")
    try:
        u8 = raw.decode("utf-8")
        if _looks_like_text(u8) and any("\u4e00" <= c <= "\u9fff" for c in u8):
            return u8
    except UnicodeDecodeError:
        pass
    try:
        g = raw.decode("gb18030")
        if _looks_like_text(g) and any(ord(c) > 127 for c in g):
            return g
    except UnicodeDecodeError:
        pass
    try:
        u8 = raw.decode("utf-8")
        if _looks_like_text(u8):
            return u8
    except UnicodeDecodeError:
        pass
    return token


def _clean_basename(name: str) -> str:
    if not name:
        return ""
    name = name.strip().strip('"').strip("'").strip()
    # 服务器偶发返回带路径的值（a/b.zip、..\\x.zip），只保留末段
    name = name.replace("\\", "/").split("/")[-1]
    # 去控制字符（含 CRLF 头注入残留）
    name = "".join(c for c in name if ord(c) >= 32)
    return name.strip()


def _filename_from_disposition(disposition: str) -> str:
    """解析 Content-Disposition（RFC 6266），``filename*`` 优先于 ``filename``。

    覆盖：``filename*=UTF-8''%E4%B8%AD...``、``filename*=GBK''...``、
    带/不带引号的 ``filename="中文.zip"``（裸 UTF-8/GBK 字节自动纠码）。
    """
    if not disposition:
        return ""
    m = re.search(r"filename\*\s*=\s*([^;]+)", disposition, re.IGNORECASE)
    if m:
        token = m.group(1).strip().strip('"').strip()
        mm = re.match(r"^([A-Za-z0-9_\-]+)'([^']*)'(.*)$", token, re.DOTALL)
        name = _percent_decode(mm.group(3), mm.group(1)) if mm \
            else _percent_decode(token, "utf-8")
        name = _clean_basename(name)
        if name:
            return name
    m = re.search(r'filename\s*=\s*("[^"]*"|\'[^\']*\'|[^;]+)',
                  disposition, re.IGNORECASE)
    if m:
        token = m.group(1).strip().strip('"').strip("'").strip()
        name = _clean_basename(_repair_legacy_filename(token))
        if name:
            return name
    return ""


def _filename_from_url(url: str) -> str:
    path = urlparse(url).path
    name = os.path.basename(path)
    return unquote(name) if name else "download"


def _safe_filename(name: str) -> str:
    name = name.replace("\x00", "").strip()
    # Windows 非法字符替换
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    return name[:180] or "download"


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_LONG_HEX_RE = re.compile(r"^[0-9a-f]{24,}$", re.IGNORECASE)
_EXT_RE = re.compile(r"^[a-z0-9]{1,6}$", re.IGNORECASE)


def meaningful_filename(name: str) -> bool:
    """判断文件名是否“像样”：有合法扩展名，且不是 UUID/长十六进制随机资源 ID。

    例如 GitHub Release 跳转到 objects.githubusercontent.com 后，最终 URL
    路径末段是 ``5dd04da9-2a59-4c10-8a5b-...`` 这类随机串（无扩展名），
    不应作为文件名。
    """
    if not name:
        return False
    base = os.path.basename(unquote(str(name)).replace("\\", "/").split("?")[0].strip())
    if not base:
        return False
    stem, ext = os.path.splitext(base)
    target = stem or base
    # UUID（8-4-4-4-12）或 16 位以上纯十六进制哈希 → 随机资源 ID
    if _UUID_RE.match(target) or re.fullmatch(r"[0-9a-f]{16,}", target, re.IGNORECASE):
        return False
    if ext and _EXT_RE.match(ext[1:].lower()):
        return True
    # 无扩展名：README/LICENSE/Makefile 这类可读词认可，纯随机串不认可
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._\-]{0,39}", target))


# 无扩展名的通用下载端点词（/dl、/download、/file、/redirect 等），不具名
_GENERIC_ENDPOINT_WORDS = {
    "download", "dl", "d", "get", "go", "file", "files", "asset", "assets",
    "fetch", "down", "redirect", "redirects", "index", "api", "item", "items",
    "share", "link", "links", "view", "preview", "stream", "attach", "attachment",
}


def _usable_url_name(name: str) -> bool:
    if not meaningful_filename(name):
        return False
    stem, ext = os.path.splitext(name)
    if not ext and stem.lower() in _GENERIC_ENDPOINT_WORDS:
        return False
    return True


def choose_url_filename(orig_url: str, final_url: str) -> str:
    """无 Content-Disposition 时，在原始 URL 与最终 URL 的路径文件名间择优。

    原始请求 URL（用户点击的链接）通常比 302 跳转后的临时签名地址更能反映
    真实文件名，故只要原始名“像样”就优先采用；通用端点词（/dl、/download）
    与随机资源 ID 都会降权，让位给最终地址里的真名。
    """
    orig = _filename_from_url(orig_url)
    final = _filename_from_url(final_url)
    if _usable_url_name(orig):
        return orig
    if _usable_url_name(final):
        return final
    return orig if orig and orig != "download" else (final or "download")


def reconcile_filename(suggested: str, probed: str) -> str:
    """合并“调用方建议名”与“探测到的服务器文件名”。

    - 建议名为空：采用探测名；
    - 建议名是正常文件名（用户手改、m3u8 合成名、扩展透传真名等）：保留；
    - 建议名是 UUID/随机资源 ID、且探测到了可信名：用探测名纠正。

    注意：无扩展名的正常文件（如 README/LICENSE）不会被误判——只有当探测端
    给出了可信名时才纠正，否则原样保留建议名。
    """
    suggested = suggested or ""
    probed = probed or ""
    if not suggested:
        return probed
    if meaningful_filename(suggested):
        return suggested
    if probed and meaningful_filename(probed):
        return probed
    return suggested


def build_session(cfg):
    """根据配置构造 HTTP 会话（代理、认证、UA、自定义头）。

    开启 ``prefer_http2`` 且本机装有 httpx[h2] 时返回 HTTP/2 会话，否则使用
    requests（HTTP/1.1）。两者对外接口兼容。
    """
    # httpx 不支持 NTLM/Negotiate 代理握手，此类代理强制走 requests
    if getattr(cfg, "prefer_http2", False) and not is_ntlm_proxy(cfg):
        try:
            from .http2 import H2Session, h2_available
            if h2_available():
                return H2Session(cfg)
        except Exception:  # noqa: BLE001  任何意外都回退到 requests
            pass
    session = requests.Session()
    headers = {"User-Agent": getattr(cfg, "user_agent", DEFAULT_USER_AGENT) or DEFAULT_USER_AGENT}
    referer = getattr(cfg, "referer", "")
    if referer:
        headers["Referer"] = referer
    extra = getattr(cfg, "headers", None)
    if extra:
        headers.update(extra)
    session.headers.update(headers)
    cookies = getattr(cfg, "cookies", None)
    if cookies:
        if isinstance(cookies, dict):
            session.cookies.update(cookies)
        else:
            session.headers["Cookie"] = str(cookies)
    # 代理与代理认证（Basic/NTLM/PAC 解析结果）统一在会话上配置
    apply_proxy_session(session, cfg)
    if not getattr(cfg, "verify_ssl", True):
        # 忽略证书校验：关闭会话校验并屏蔽 urllib3 不安全警告
        session.verify = False
        tls.silence_insecure_warnings()
    else:
        # 默认改用操作系统证书库（含企业内网 CA）；不可用时回退 certifi
        tls.mount_system_ca(session)
    return session


def session_kwargs(cfg) -> dict:
    kwargs = {
        "timeout": (getattr(cfg, "connect_timeout", 10), getattr(cfg, "read_timeout", 30)),
        "allow_redirects": True,
        "verify": getattr(cfg, "verify_ssl", True),
    }
    # 代理由 build_session 在会话层配置（含凭据/NTLM hook），这里不再传
    # proxies，以免请求级无凭据代理覆盖掉会话配置。
    # 目标站点 HTTP 认证：NTLM 代理已占用 session.auth，请求级 auth 会将其
    # 覆盖导致代理握手失败，故该组合下以代理认证为先。
    user = getattr(cfg, "username", "") or ""
    pwd = getattr(cfg, "password", "") or ""
    if user and not is_ntlm_proxy(cfg):
        kwargs["auth"] = (user, pwd)
    return kwargs


def probe(url: str, cfg) -> FileInfo:
    """探测目标文件。cfg 为 Settings 或提供同名属性的对象。"""
    sess = build_session(cfg)
    try:
        resp = sess.get(
            url,
            headers={"Range": "bytes=0-0"},
            stream=True,
            **session_kwargs(cfg),
        )
    except requests.RequestException as exc:
        raise ProbeError(tls.with_cert_hint(f"连接失败: {exc}", exc)) from exc

    try:
        status = resp.status_code
        if status >= 400:
            raise ProbeError(f"服务器返回 HTTP {status}")
        h = resp.headers
        info = FileInfo(url=url, final_url=resp.url, status_code=status, headers=dict(h))
        info.etag = h.get("ETag", "")
        info.last_modified = h.get("Last-Modified", "")

        if status == 206:
            info.resumable = True
            cr = h.get("Content-Range", "")
            m = _RANGE_RE.search(cr)
            if m and m.group(1):
                info.total_size = int(m.group(1))
            else:
                cl = h.get("Content-Length")
                info.total_size = int(cl) if cl else None
        else:
            info.resumable = False
            cl = h.get("Content-Length")
            info.total_size = int(cl) if cl and cl.isdigit() else None

        name = _filename_from_disposition(h.get("Content-Disposition", ""))
        if not name:
            # 无 Content-Disposition 时，原始请求 URL（用户点击的链接）通常
            # 比 302 跳转后的临时地址（末段可能是随机资源 ID）更能反映真实文件名
            name = choose_url_filename(url, resp.url)
        name = _safe_filename(name)
        # 头部与路径都给不出可用扩展名时，按 Content-Type 补一个（保证可打开/分类）
        if not _has_proper_ext(name):
            ctype = h.get("Content-Type", "").split(";", 1)[0].strip().lower()
            ext = _MIME_EXT_FALLBACK.get(ctype, "")
            if ext:
                name = _safe_filename(name + ext)
        info.filename = name
        return info
    finally:
        resp.close()
        sess.close()
