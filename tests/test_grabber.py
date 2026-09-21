# -*- coding: utf-8 -*-
"""网页资源抓取测试（本地 HTTP 站点，无需外网）。

运行: python -m unittest tests.test_grabber -v
"""
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_TMP_APPDATA = tempfile.mkdtemp(prefix="pydl-grab-appdata-")
os.environ["APPDATA"] = _TMP_APPDATA
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from core.config import Settings  # noqa: E402
from core.grabber import extract_links, grab  # noqa: E402

INDEX = """
<html><body>
  <a href="/files/a.zip">zip</a>
  <a href="https://other.example.com/x.exe">external</a>
  <a href="/page2.html">page2</a>
  <a href="/about">about (no ext)</a>
  <video><source src="/media/v.mp4"></video>
  <audio src="/media/song.mp3"></audio>
  <img src="/img/logo.png">
  <img src="/secret/hidden.mp4">
</body></html>
"""
PAGE2 = """
<html><body><a href="/files/b.pdf">doc</a>
<video src="/media/clip.webm"></video></body></html>
"""
ROBOTS = "User-agent: *\nDisallow: /secret/\n"


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype="text/html; charset=utf-8"):
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Type", ctype)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?", 1)[0]
        if p == "/robots.txt":
            return self._send(ROBOTS.encode(), "text/plain")
        if p in ("/index.html", "/"):
            return self._send(INDEX.encode())
        if p == "/page2.html":
            return self._send(PAGE2.encode())
        if p == "/about":
            return self._send(b"<html></html>")
        # 其它资源路径返回占位二进制
        return self._send(b"\x00\x01\x02\x03", "application/octet-stream")


class GrabServer:
    def __init__(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever,
                                       daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.httpd.shutdown()


class ExtractTests(unittest.TestCase):
    def test_extract_media_and_anchors(self):
        media, anchors = extract_links(INDEX, "http://x/index.html")
        self.assertIn("http://x/media/v.mp4", media)
        self.assertIn("http://x/img/logo.png", media)
        self.assertIn("http://x/files/a.zip", anchors)
        self.assertIn("http://x/page2.html", anchors)


class GrabTests(unittest.TestCase):
    def setUp(self):
        self.cfg = Settings()
        self.cfg.av_scan = False

    def test_depth0_same_host(self):
        with GrabServer() as s:
            url = f"http://127.0.0.1:{s.port}/index.html"
            found = grab(url, self.cfg, same_host=True, depth=0)
        joined = set(found)
        self.assertIn(f"http://127.0.0.1:{s.port}/files/a.zip", joined)
        self.assertIn(f"http://127.0.0.1:{s.port}/media/v.mp4", joined)
        self.assertIn(f"http://127.0.0.1:{s.port}/img/logo.png", joined)
        # 外链被同站过滤
        self.assertNotIn("https://other.example.com/x.exe", joined)
        # depth=0 不抓子页面里的资源
        self.assertFalse(any(u.endswith("b.pdf") for u in joined))
        self.assertFalse(any(u.endswith("clip.webm") for u in joined))

    def test_depth1_follows_html(self):
        with GrabServer() as s:
            url = f"http://127.0.0.1:{s.port}/index.html"
            found = grab(url, self.cfg, same_host=True, depth=1)
        joined = "\n".join(found)
        self.assertIn("/files/b.pdf", joined)
        self.assertIn("/media/clip.webm", joined)

    def test_category_filter(self):
        with GrabServer() as s:
            url = f"http://127.0.0.1:{s.port}/index.html"
            found = grab(url, self.cfg, categories={"视频"}, depth=0)
        joined = "\n".join(found)
        self.assertIn("/media/v.mp4", joined)
        self.assertNotIn("/files/a.zip", joined)
        self.assertNotIn("/img/logo.png", joined)

    def test_robots_respected(self):
        with GrabServer() as s:
            url = f"http://127.0.0.1:{s.port}/index.html"
            found = grab(url, self.cfg, respect_robots=True, depth=0)
            ignored = grab(url, self.cfg, respect_robots=False, depth=0)
        self.assertFalse(any("/secret/" in u for u in found))
        self.assertTrue(any("/secret/" in u for u in ignored))


if __name__ == "__main__":
    unittest.main(verbosity=2)
