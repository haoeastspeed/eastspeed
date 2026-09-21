# -*- coding: utf-8 -*-
"""内存 HLS 测试服务器：明文 TS、AES-128、master 多码率、fMP4、直播流。"""
from __future__ import annotations

import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def _pkcs7_encrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(data) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return enc.update(padded) + enc.finalize()


def _seg_iv(seq: int) -> bytes:
    return seq.to_bytes(16, "big")


def build_fixtures():
    """生成全部测试素材（确定性随机）。"""
    import random
    rnd = random.Random(20260919)
    n = 8
    seg_size = 80 * 1024
    plain = [bytes(rnd.getrandbits(8) for _ in range(seg_size)) for _ in range(n)]

    high = [b"HIGH" + bytes(rnd.getrandbits(8) for _ in range(seg_size)) for _ in range(6)]
    low = [b"LOW" + bytes(rnd.getrandbits(8) for _ in range(seg_size // 2)) for _ in range(3)]

    key = bytes(range(16))
    enc = [_pkcs7_encrypt(plain[i], key, _seg_iv(i)) for i in range(n)]

    init = b"INIT-MP4-" + bytes(rnd.getrandbits(8) for _ in range(4096))
    m4s = [b"FRAG" + str(i).encode() + b"-" +
           bytes(rnd.getrandbits(8) for _ in range(40 * 1024)) for i in range(4)]
    return {"plain": plain, "high": high, "low": low, "key": key,
            "enc": enc, "init": init, "m4s": m4s}


def _media_m3u8(prefix: str, n: int, seg_tpl: str, enc: bool = False,
                map_line: str = "") -> bytes:
    lines = ["#EXTM3U", "#EXT-X-VERSION:7" if map_line else "#EXT-X-VERSION:3",
             "#EXT-X-TARGETDURATION:4", "#EXT-X-MEDIA-SEQUENCE:0"]
    if enc:
        lines.append('#EXT-X-KEY:METHOD=AES-128,URI="enc.key"')
    if map_line:
        lines.append(map_line)
    for i in range(n):
        lines.append("#EXTINF:4.0,")
        lines.append(seg_tpl.format(i=i))
    lines.append("#EXT-X-ENDLIST")
    return ("\n".join(lines)).encode()


def _live_m3u8() -> bytes:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:4",
             "#EXT-X-MEDIA-SEQUENCE:100"]
    for i in range(3):
        lines.append("#EXTINF:4.0,")
        lines.append(f"seg{i}.ts")
    return ("\n".join(lines)).encode()  # 无 ENDLIST


class _Handler(BaseHTTPRequestHandler):
    fx = None
    seg_delay = 0.0

    def log_message(self, *args):
        pass

    def _maybe_delay(self, path: str):
        if self.seg_delay and (".ts" in path or ".m4s" in path or "seg" in path):
            time.sleep(self.seg_delay)

    def _send(self, body: bytes, ctype: str = "application/octet-stream"):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        fx = self.fx
        path = self.path
        self._maybe_delay(path)
        m3u8 = "application/vnd.apple.mpegurl"
        try:
            if path == "/vod/media.m3u8":
                return self._send(_media_m3u8("/vod", len(fx["plain"]), "seg{i}.ts"), m3u8)
            if path.startswith("/vod/seg") and path.endswith(".ts"):
                i = int(path.split("seg")[1].split(".")[0])
                return self._send(fx["plain"][i])

            if path == "/vod/master.m3u8":
                body = ("#EXTM3U\n"
                        "#EXT-X-STREAM-INF:BANDWIDTH=500000\nlow.m3u8\n"
                        "#EXT-X-STREAM-INF:BANDWIDTH=2000000\nhigh.m3u8\n").encode()
                return self._send(body, m3u8)
            if path == "/vod/high.m3u8":
                return self._send(_media_m3u8("/vod", len(fx["high"]), "high{i}.ts"), m3u8)
            if path == "/vod/low.m3u8":
                return self._send(_media_m3u8("/vod", len(fx["low"]), "low{i}.ts"), m3u8)
            if path.startswith("/vod/high"):
                i = int(path.split("high")[1].split(".")[0])
                return self._send(fx["high"][i])
            if path.startswith("/vod/low"):
                i = int(path.split("low")[1].split(".")[0])
                return self._send(fx["low"][i])

            if path == "/enc/media.m3u8":
                return self._send(_media_m3u8("/enc", len(fx["enc"]), "seg{i}.ts", enc=True), m3u8)
            if path == "/enc/enc.key":
                return self._send(fx["key"], "application/octet-stream")
            if path.startswith("/enc/seg"):
                i = int(path.split("seg")[1].split(".")[0])
                return self._send(fx["enc"][i])

            if path == "/fmp4/media.m3u8":
                return self._send(
                    _media_m3u8("/fmp4", len(fx["m4s"]), "frag{i}.m4s",
                               map_line='#EXT-X-MAP:URI="init.mp4"'), m3u8)
            if path == "/fmp4/init.mp4":
                return self._send(fx["init"])
            if path.startswith("/fmp4/frag"):
                i = int(path.split("frag")[1].split(".")[0])
                return self._send(fx["m4s"][i])

            if path == "/live/media.m3u8":
                return self._send(_live_m3u8(), m3u8)

            self.send_error(404)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass


def make_hls_server(port: int = 0, seg_delay: float = 0.0):
    class Handler(_Handler):
        pass
    Handler.fx = build_fixtures()
    Handler.seg_delay = seg_delay
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    actual = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{actual}", Handler.fx
