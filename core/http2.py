# -*- coding: utf-8 -*-
"""可选的 HTTP/2 传输层（基于 httpx + h2）。

对外暴露与 ``requests.Session`` / ``requests.Response`` 足够兼容的薄包装，使
探测与多连接下载代码无需改动即可在 HTTP/2 上运行：

- 每个 :class:`H2Session` 内部持有一个独立 ``httpx.Client``，下载引擎为每个
  worker 新建一个会话，因此在 HTTP/2 服务器上仍是多条独立 TCP+TLS 连接并行请求
  不同 Range（HTTP/2 多路复用在单连接内同样有效）；
- 明文 ``http://`` 站点 httpx 会自动回退 HTTP/1.1；HTTP/2 主要对 ``https://`` 生效；
- 缺少 httpx/h2 依赖时 :func:`h2_available` 返回 False，引擎静默回退 requests；
- 所有 httpx 异常被转换为对应的 ``requests`` 异常，复用现有重试/看门狗逻辑。
"""
from __future__ import annotations

import importlib.util

import requests

from .probe import DEFAULT_USER_AGENT


def h2_available() -> bool:
    return (importlib.util.find_spec("httpx") is not None
            and importlib.util.find_spec("h2") is not None)


def _convert(exc: Exception) -> Exception:
    """把 httpx 异常映射为 requests 异常体系。"""
    import httpx
    if isinstance(exc, httpx.TimeoutException):
        return requests.Timeout(str(exc))
    # NetworkError 是 Connect/Read/Write/CloseError 的共同基类；
    # RemoteProtocolError 表示对端中断/协议异常，同样按可重试的连接错误处理。
    if isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError)):
        return requests.ConnectionError(str(exc))
    if isinstance(exc, httpx.HTTPError):
        return requests.RequestException(str(exc))
    return exc


class H2Response:
    def __init__(self, resp, stream: bool):
        self._r = resp
        self._stream = stream
        self.status_code = resp.status_code
        self.headers = resp.headers
        self._content = None

    @property
    def url(self) -> str:
        return str(self._r.url)

    def iter_content(self, chunk_size: int = 65536):
        try:
            for chunk in self._r.iter_bytes(chunk_size):
                if chunk:
                    yield chunk
        except Exception as exc:  # noqa: BLE001
            mapped = _convert(exc)
            if isinstance(mapped, requests.RequestException):
                raise mapped from exc
            raise

    @property
    def content(self) -> bytes:
        if self._content is None:
            try:
                self._content = self._r.read()
            except Exception as exc:  # noqa: BLE001
                mapped = _convert(exc)
                if isinstance(mapped, requests.RequestException):
                    raise mapped from exc
                raise
        return self._content

    @property
    def text(self) -> str:
        return self._r.text

    def close(self):
        try:
            self._r.close()
        except Exception:  # noqa: BLE001
            pass


class H2Session:
    """requests.Session 的子集兼容实现，底层为 httpx（HTTP/2）。"""

    def __init__(self, cfg):
        if not h2_available():
            raise RuntimeError("httpx[h2] 不可用")
        import httpx

        self.cfg = cfg
        headers = {
            "User-Agent": getattr(cfg, "user_agent", "") or DEFAULT_USER_AGENT,
        }
        referer = getattr(cfg, "referer", "")
        if referer:
            headers["Referer"] = referer
        extra = getattr(cfg, "headers", None)
        if extra:
            headers.update(extra)
        cookies = getattr(cfg, "cookies", None)
        cookie_dict = None
        if isinstance(cookies, dict):
            cookie_dict = cookies
        elif cookies:
            headers["Cookie"] = str(cookies)

        timeout = httpx.Timeout(
            getattr(cfg, "read_timeout", 30),
            connect=getattr(cfg, "connect_timeout", 10),
        )
        kwargs = dict(
            http2=True, timeout=timeout, follow_redirects=True,
            headers=headers, verify=getattr(cfg, "verify_ssl", True),
        )
        proxy = getattr(cfg, "proxy", "") or ""
        if proxy:
            # httpx 仅支持代理 Basic 认证（URL 内嵌凭据）；NTLM/Negotiate
            # 已在 build_session 层回退到 requests，不会进入这里。
            from urllib.parse import quote
            from .proxy_support import proxy_credentials
            scheme, hostport, puser, ppwd = proxy_credentials(cfg)
            if puser:
                kwargs["proxy"] = (
                    f"{scheme}://{quote(puser, safe='')}:"
                    f"{quote(ppwd or '', safe='')}@{hostport}")
            else:
                kwargs["proxy"] = f"{scheme}://{hostport}"
        user = getattr(cfg, "username", "") or ""
        if user:
            kwargs["auth"] = (user, getattr(cfg, "password", "") or "")
        if cookie_dict:
            kwargs["cookies"] = cookie_dict
        self._client = httpx.Client(**kwargs)
        self.headers = self._client.headers

    def get(self, url, headers=None, stream=False, **_ignored):
        import httpx
        merged = dict(headers or {})
        try:
            req = self._client.build_request("GET", url, headers=merged)
            resp = self._client.send(req, stream=stream)
        except Exception as exc:  # noqa: BLE001
            mapped = _convert(exc)
            if isinstance(mapped, requests.RequestException):
                raise mapped from exc
            raise
        return H2Response(resp, stream)

    def close(self):
        try:
            self._client.close()
        except Exception:  # noqa: BLE001
            pass
