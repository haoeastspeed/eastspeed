# -*- coding: utf-8 -*-
"""代理支持：Basic / NTLM(含 Negotiate) 认证与 PAC 自动配置。

- Basic 认证：把凭据安全地嵌入代理 URL，交给 urllib3 原生处理 407；
- NTLM / Negotiate：使用可选依赖 requests_ntlm（其 response hook 已原生
  处理 407 Proxy-Authenticate），挂到 session.auth；
- PAC：Windows 优先用系统 WinHTTP 引擎解析（与 IE/Edge 同一套，原生执行
  PAC 脚本），其它平台尝试可选依赖 pypac，均不可用时回退直连。
"""
from __future__ import annotations

import sys
from urllib.parse import urlparse, quote

from .errors import DownloadError


def normalize_proxy(proxy: str) -> str:
    """补全 scheme、去掉路径，返回 host:port 形式的代理 URL。"""
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = "http://" + proxy
    p = urlparse(proxy)
    port = p.port or (443 if p.scheme == "https" else 8080)
    return f"{p.scheme}://{p.hostname}:{port}"


def proxy_credentials(cfg) -> tuple[str, str, str, str]:
    """返回 (scheme, host:port, username, password)，凭据取显式字段或 URL。"""
    raw = (getattr(cfg, "proxy", "") or "").strip()
    if "://" not in raw:
        raw = "http://" + raw
    p = urlparse(raw)
    port = p.port or (443 if p.scheme == "https" else 8080)
    user = getattr(cfg, "proxy_username", None) or p.username or ""
    pwd = getattr(cfg, "proxy_password", None)
    if pwd is None:
        pwd = p.password or ""
    return p.scheme or "http", f"{p.hostname}:{port}", user, pwd


def is_ntlm_proxy(cfg) -> bool:
    auth = (getattr(cfg, "proxy_auth", "") or "").lower()
    if auth in ("ntlm", "negotiate"):
        return True
    # 填了 NTLM 域名但未显式选认证方式时也按 NTLM
    return auth == "" and bool(getattr(cfg, "proxy_domain", None))


def apply_proxy_session(session, cfg) -> None:
    """把代理与认证配置到 requests.Session（就地修改）。"""
    proxy = (getattr(cfg, "proxy", "") or "").strip()
    if not proxy:
        return
    scheme, hostport, user, pwd = proxy_credentials(cfg)
    base = f"{scheme}://{hostport}"

    if is_ntlm_proxy(cfg):
        try:
            from requests_ntlm import HttpNtlmAuth
        except ImportError as e:
            raise DownloadError(
                "NTLM/Negotiate 代理认证需要可选依赖 requests_ntlm，"
                "请执行：pip install requests_ntlm") from e
        domain = (getattr(cfg, "proxy_domain", "") or "").strip()
        if domain and user and "\\" not in user and "@" not in user:
            user = f"{domain}\\{user}"
        uname = user
        session.auth = HttpNtlmAuth(uname, pwd or "")
        session.proxies.update({"http": base, "https": base})
        session.headers["Connection"] = "Keep-Alive"
        return

    if user:  # Basic：凭据嵌入代理 URL，urllib3 自动完成 407 Basic 握手
        cred = f"{quote(user, safe='')}:{quote(pwd or '', safe='')}"
        with_auth = f"{scheme}://{cred}@{hostport}"
        session.proxies.update({"http": with_auth, "https": with_auth})
    else:
        session.proxies.update({"http": base, "https": base})


# ---------------- PAC ----------------

def _first_proxy_from_directive(directive: str) -> str:
    """解析 'PROXY host:port; PROXY host2:port; DIRECT' 为首个代理 URL。"""
    if not directive:
        return ""
    for part in directive.split(";"):
        part = part.strip()
        if not part:
            continue
        upper = part.upper()
        if upper.startswith("PROXY") or upper.startswith("HTTP"):
            host = part.split(" ", 1)[-1].strip()
            if host:
                return normalize_proxy(host)
        if upper.startswith("SOCKS"):
            host = part.split(" ", 1)[-1].strip()
            if host:
                return "socks5://" + host
        if upper == "DIRECT":
            return ""
    return ""


def _resolve_pac_winhttp(pac_url: str, target_url: str) -> str | None:
    """用 Windows WinHTTP 原生引擎执行 PAC 并返回代理 URL；失败返回 None。"""
    if not sys.platform.startswith("win"):
        return None
    import ctypes
    from ctypes import wintypes
    HINTERNET = ctypes.c_void_p  # wintypes 未定义 HINTERNET

    try:
        winhttp = ctypes.WinDLL("winhttp.dll")
    except Exception:
        return None

    class AUTOPROXY_OPTIONS(ctypes.Structure):
        _fields_ = [
            ("dwFlags", wintypes.DWORD),
            ("dwAutoDetectFlags", wintypes.DWORD),
            ("lpszAutoConfigUrl", wintypes.LPCWSTR),
            ("lpvAutoDetectBuffer", ctypes.c_void_p),
            ("dwAutoDetectBufferSize", wintypes.DWORD),
            ("lpszProxy", wintypes.LPCWSTR),
            ("lpszProxyBypass", wintypes.LPCWSTR),
            ("fAutoLogonIfChallenged", wintypes.BOOL),
        ]

    class PROXY_INFO(ctypes.Structure):
        # 用 c_void_p 保留原始指针，避免 ctypes 转成 str 后再释放导致堆损坏
        _fields_ = [
            ("dwAccessType", wintypes.DWORD),
            ("lpszProxy", ctypes.c_void_p),
            ("lpszProxyBypass", ctypes.c_void_p),
        ]

    WINHTTP_ACCESS_TYPE_NO_PROXY = 1
    WINHTTP_AUTOPROXY_CONFIG_URL = 0x00000002
    WINHTTP_OPTION_REDIRECT_POLICY_ALWAYS = 0x33000003

    winhttp.WinHttpOpen.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR,
        wintypes.LPCWSTR, wintypes.DWORD]
    winhttp.WinHttpOpen.restype = HINTERNET
    winhttp.WinHttpGetProxyForUrl.argtypes = [
        HINTERNET, wintypes.LPCWSTR,
        ctypes.POINTER(AUTOPROXY_OPTIONS), ctypes.POINTER(PROXY_INFO)]
    winhttp.WinHttpGetProxyForUrl.restype = wintypes.BOOL
    winhttp.WinHttpCloseHandle.argtypes = [HINTERNET]

    session = winhttp.WinHttpOpen(
        "DongFangSpeed/1.0", WINHTTP_ACCESS_TYPE_NO_PROXY,
        None, None, 0)
    if not session:
        return None
    try:
        opts = AUTOPROXY_OPTIONS()
        opts.dwFlags = WINHTTP_AUTOPROXY_CONFIG_URL
        opts.lpszAutoConfigUrl = pac_url
        opts.fAutoLogonIfChallenged = True
        info = PROXY_INFO()
        ok = winhttp.WinHttpGetProxyForUrl(
            session, target_url, ctypes.byref(opts), ctypes.byref(info))
        if not ok:
            return None
        raw = ctypes.wstring_at(info.lpszProxy) if info.lpszProxy else ""
        try:
            k32 = ctypes.windll.kernel32
            k32.GlobalFree.argtypes = [ctypes.c_void_p]
            k32.GlobalFree.restype = ctypes.c_void_p
            if info.lpszProxy:
                k32.GlobalFree(info.lpszProxy)
            if info.lpszProxyBypass:
                k32.GlobalFree(info.lpszProxyBypass)
        except Exception:
            pass
        # 形如 "http=host:port;https=host:port" 或 "host:port"
        directive = ""
        if "=" in raw:
            for kv in raw.split(";"):
                k, _, v = kv.partition("=")
                if k.strip().lower() in ("http", "https") and v.strip():
                    directive = f"PROXY {v.strip()}"
                    break
        else:
            directive = f"PROXY {raw.strip()}"
        return _first_proxy_from_directive(directive)
    except Exception:
        return None
    finally:
        winhttp.WinHttpCloseHandle(session)


def _resolve_pac_pypac(pac_url: str, target_url: str) -> str | None:
    try:
        import pypac  # type: ignore
    except Exception:
        return None
    try:
        pac = pypac.get_pac(url=pac_url)
        if pac is None:
            return None
        host = urlparse(target_url).hostname or ""
        directive = pac.find_proxy_for_url(target_url, host)
        return _first_proxy_from_directive(directive or "")
    except Exception:
        return None


def resolve_pac_proxy(pac_url: str, target_url: str) -> str | None:
    """按 PAC 脚本解析目标 URL 的代理；DIRECT/失败返回 "" 或 None。"""
    if not pac_url:
        return None
    result = _resolve_pac_winhttp(pac_url, target_url)
    if result is not None:
        return result
    result = _resolve_pac_pypac(pac_url, target_url)
    return result


def resolve_effective_proxy(cfg, target_url: str) -> str:
    """返回对该目标 URL 实际应使用的代理 URL（先 PAC，后固定代理）。"""
    pac = (getattr(cfg, "pac_url", "") or "").strip()
    if pac:
        resolved = resolve_pac_proxy(pac, target_url)
        if resolved:
            return resolved
        # PAC 判定 DIRECT 或解析失败 -> 直连
        if resolved is not None:
            return ""
    return (getattr(cfg, "proxy", "") or "").strip()
