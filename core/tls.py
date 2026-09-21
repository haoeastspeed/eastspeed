# -*- coding: utf-8 -*-
"""TLS 信任策略（非侵入式）。

默认让 HTTP/1.1 下载（requests/urllib3）使用 **操作系统证书库**（Windows 上即
受信任的根证书颁发机构，包含企业通过 AD/组策略推送的内网 CA），而不是仅用
requests 自带的 certifi——否则访问内网/自签 HTTPS 站点会报
``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate``。

实现方式是给 requests 会话**显式挂载**一个 truststore 客户端 SSLContext
（经自定义 HTTPAdapter 注入 urllib3 连接池），**不**全局替换 ``ssl.SSLContext``，
因此 HTTP/2(httpx)、本地 TLS 测试服务器等不受影响。truststore 缺失时安静回退到
certifi 默认行为。

``verify_ssl=False``（设置中“忽略 HTTPS 证书错误”）则完全不校验，作为自签证书
且系统库也不信任时的兜底。
"""
from __future__ import annotations

import ssl

CERT_HINT = (
    "服务器 HTTPS 证书校验失败（内网/自签证书常见，本机缺少签发它的根证书）。\n"
    "解决办法二选一：\n"
    "1）【推荐】把企业根证书安装到 Windows“受信任的根证书颁发机构”；\n"
    "2）临时使用：在“设置 → 高级”勾选“忽略 HTTPS 证书错误”（不安全，仅用于可信内网）。"
)


def system_ssl_context():
    """返回基于操作系统证书库的客户端 SSLContext；truststore 不可用时返回 None。"""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:  # noqa: BLE001
        return None


def mount_system_ca(session) -> bool:
    """把系统证书库 SSLContext 挂到 requests 会话（http/https 均挂载）。

    成功返回 True；truststore 不可用返回 False（调用方保持 certifi 默认即可）。
    """
    ctx = system_ssl_context()
    if ctx is None:
        return False
    try:
        from requests.adapters import HTTPAdapter

        class _SystemCAAdapter(HTTPAdapter):
            def init_poolmanager(self, connections, maxsize, **kw):
                kw["ssl_context"] = ctx
                return super().init_poolmanager(connections, maxsize, **kw)

            def proxy_manager_for(self, proxy, **kw):
                kw["ssl_context"] = ctx
                return super().proxy_manager_for(proxy, **kw)

        adapter = _SystemCAAdapter()
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return True
    except Exception:  # noqa: BLE001
        return False


def requests_session():
    """新建一个已挂载操作系统证书库的 requests.Session（供独立网络模块使用）。"""
    import requests
    sess = requests.Session()
    mount_system_ca(sess)
    return sess


def is_certificate_error(exc: BaseException) -> bool:
    """沿异常因果链判断是否为证书校验失败（requests/urllib3/ssl 包装层级不一）。"""
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, ssl.SSLCertVerificationError):
            return True
        text = str(cur)
        if ("CERTIFICATE_VERIFY_FAILED" in text
                or "unable to get local issuer certificate" in text
                or "certificate verify failed" in text):
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def with_cert_hint(message: str, exc: BaseException) -> str:
    """若异常是证书错误，在原消息后追加可操作的中文提示。"""
    if is_certificate_error(exc):
        return f"{message}\n{CERT_HINT}"
    return message


def silence_insecure_warnings():
    """verify=False 时关闭 urllib3 的 InsecureRequestWarning（每进程一次）。"""
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    except Exception:  # noqa: BLE001
        pass


def native_server_context(protocol: int = ssl.PROTOCOL_TLS_SERVER):
    """返回标准库原生 SSLContext（供本地/测试 TLS 服务器使用）。"""
    return ssl.SSLContext(protocol)
