# -*- coding: utf-8 -*-
"""网页资源批量抓取（有限深度的“整站抓取”，不是无限爬虫）。

给定一个网页 URL，解析其中的媒体/文件链接，返回去重后的绝对 URL 列表，交给
下载引擎批量建任务：

- 默认 ``depth=0``：只解析当前页面；``depth=1`` 时额外跟进同站的 HTML 子页面；
- 默认只保留与页面同主机的链接（``same_host``），避免把外链统计脚本也抓回来；
- ``<a href>`` 仅在指向具体文件扩展名时收录，``<img>/<video>/<audio>/<source>``
  等媒体标签默认收录；
- 默认遵守简单的 ``robots.txt`` Disallow 规则。
"""
from __future__ import annotations

import urllib.parse
from html.parser import HTMLParser

from .category import host_of
from .probe import build_session, session_kwargs

# 可作为下载目标的资源扩展名（按类别）
RESOURCE_EXTS = {
    "视频": {"mp4", "webm", "mkv", "mov", "avi", "m4v", "ts", "flv", "wmv",
            "m3u8"},
    "音频": {"mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus"},
    "图片": {"jpg", "jpeg", "png", "gif", "webp", "svg", "bmp", "ico", "tiff"},
    "文档": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt",
            "epub", "mobi", "csv", "rtf"},
    "压缩包": {"zip", "rar", "7z", "gz", "tar", "bz2", "xz"},
    "程序": {"exe", "msi", "apk", "dmg", "deb", "rpm", "bin", "appimage"},
}
ALL_EXTS: dict[str, str] = {}
for _cat, _exts in RESOURCE_EXTS.items():
    for _e in _exts:
        ALL_EXTS[_e] = _cat

MEDIA_TAGS = {
    "img": "src",
    "audio": "src",
    "video": "src",
    "source": "src",
    "embed": "src",
    "track": "src",
}


def ext_of(url: str) -> str:
    path = urllib.parse.urlparse(url).path.lower()
    ext = path.rsplit(".", 1)[-1] if "." in path else ""
    return ext


def category_of_resource(url: str) -> str:
    return ALL_EXTS.get(ext_of(url), "")


class _LinkParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.media: list[str] = []
        self.anchors: list[str] = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in MEDIA_TAGS:
            src = attrs.get(MEDIA_TAGS[tag]) or attrs.get("data-src")
            if src:
                self.media.append(self._abs(src))
        elif tag == "a":
            href = attrs.get("href")
            if href:
                self.anchors.append(self._abs(href))

    def _abs(self, url: str) -> str:
        url = url.strip()
        if url.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            return ""
        return urllib.parse.urljoin(self.base_url, url)


def extract_links(html: str, base_url: str) -> tuple[list[str], list[str]]:
    """返回 (媒体链接, 锚点链接) 绝对 URL 列表（不去重）。"""
    parser = _LinkParser(base_url)
    try:
        parser.feed(html)
    except Exception:  # noqa: BLE001  页面 HTML 可能畸形，尽力解析
        pass
    media = [u for u in parser.media if u]
    anchors = [u for u in parser.anchors if u]
    return media, anchors


def _is_html(url: str) -> bool:
    ext = ext_of(url)
    return ext in ("", "html", "htm", "php", "asp", "aspx", "jsp") or "/" in url


class _Robots:
    """极简 robots.txt 规则：仅解析通配 User-agent 组的 Disallow 前缀。"""

    def __init__(self, content: str):
        self.disallow: list[str] = []
        active = False
        for raw in content.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip().lower(), val.strip()
            if key == "user-agent":
                active = val == "*"
            elif key == "disallow" and active and val:
                self.disallow.append(urllib.parse.unquote(val))

    def allowed(self, url: str) -> bool:
        path = urllib.parse.urlparse(url).path or "/"
        for rule in self.disallow:
            if rule == "/":
                return False
            if "*" in rule:
                prefix = rule.split("*", 1)[0]
                if path.startswith(prefix):
                    return False
            elif path.startswith(rule):
                return False
        return True


def fetch_robots(page_url: str, cfg) -> _Robots | None:
    parsed = urllib.parse.urlparse(page_url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    sess = build_session(cfg)
    try:
        resp = sess.get(robots_url, **session_kwargs(cfg))
        if getattr(resp, "status_code", 200) >= 400:
            return None
        text = resp.text if hasattr(resp, "text") else resp.content.decode(
            "utf-8", "ignore")
        return _Robots(text)
    except Exception:  # noqa: BLE001
        return None
    finally:
        sess.close()


def _dedupe(items) -> list[str]:
    seen, out = set(), []
    for u in items:
        key = u.split("#", 1)[0]
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out


def grab(page_url: str, cfg, same_host: bool = True,
         categories: "set[str] | None" = None, depth: int = 0,
         respect_robots: bool = True) -> list[str]:
    """抓取页面内符合条件的资源 URL。

    categories 为允许的类别集合（见 RESOURCE_EXTS 键）；None 表示全部。
    """
    page_host = host_of(page_url)
    robots = fetch_robots(page_url, cfg) if respect_robots else None

    def wanted(url: str) -> bool:
        scheme = urllib.parse.urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            return False
        if same_host and host_of(url) != page_host:
            return False
        if robots and not robots.allowed(url):
            return False
        cat = category_of_resource(url)
        if not cat:
            return False
        return categories is None or cat in categories

    sess = build_session(cfg)
    visited_pages: set[str] = set()
    found: list[str] = []
    try:
        pages = [page_url]
        for level in range(max(0, depth) + 1):
            next_pages: list[str] = []
            for purl in pages:
                if purl in visited_pages:
                    continue
                visited_pages.add(purl)
                if robots and not robots.allowed(purl):
                    continue
                try:
                    resp = sess.get(purl, **session_kwargs(cfg))
                    if getattr(resp, "status_code", 200) >= 400:
                        continue
                    html = resp.text if hasattr(resp, "text") else \
                        resp.content.decode("utf-8", "ignore")
                except Exception:  # noqa: BLE001
                    continue
                media, anchors = extract_links(html, str(getattr(resp, "url", purl)))
                for u in media:
                    if wanted(u):
                        found.append(u)
                for u in anchors:
                    if category_of_resource(u):
                        if wanted(u):
                            found.append(u)
                    elif level < depth and _is_html(u) and (
                            not same_host or host_of(u) == page_host):
                        next_pages.append(u)
            pages = next_pages
    finally:
        sess.close()
    return _dedupe(found)
