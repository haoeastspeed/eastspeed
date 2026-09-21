# -*- coding: utf-8 -*-
"""用于端到端测试的本地 HTTP 服务器（含多种“病态服务器”场景）。

路由：
- /file/<name>        标准静态文件，完整支持 Range（206）
- /norange/<name>     忽略 Range 头，始终 200 整文件返回（测单线程回退）
- /redir/<name>       302 到随机 token 的 CDN 直链（测重定向取名）
- /cdn/<token>?f=name / /cdn_nocd/...  模拟对象存储签名直链

URL 查询参数（可叠加）：
- kbps=N       每条连接限速 N KB/s（验证多连接聚合提速）
- slowpct=P    每条 TCP 连接有 P% 概率成为“慢连接”（速率按 client 端口
               确定性派生，同一连接多次请求保持稳定），用于制造 straggler、
               验证块内工作窃取
- slowdiv=D    慢连接速率 = kbps / D（默认 4）
- jitter=MS    每个 32KB 分片前随机停顿 0~MS 毫秒（延迟抖动）
- drop=P       每条连接有 P% 概率在发送到 10%~70% 时强制中断（随机断流），
               客户端应靠重试 + 分段重领最终下完整文件
- maxconn=N    服务器级并发连接上限（信号量排队），模拟单 IP 连接数限制
- close=1      响应后关闭 TCP 连接（Connection: close），测无 keep-alive
"""
from __future__ import annotations

import mimetypes
import os
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

RANGE_RE = re.compile(r"bytes=(\d+)-(\d*)")


def _content_type(fpath: str) -> str:
    return mimetypes.guess_type(fpath)[0] or "application/octet-stream"


class _Handler(BaseHTTPRequestHandler):
    serve_dir = ""
    protocol_version = "HTTP/1.1"  # 更贴近真实服务器，Chrome 多连接下载需要

    def log_message(self, *args):
        import sys
        import os as _os
        if _os.environ.get("DFS_HTTP_LOG"):
            sys.stderr.write("[testsrv] %s %s range=%r\n" % (
                self.command, self.path, self.headers.get("Range", "")))

    def log_error(self, *args):
        # 健康监测/断流测试会故意断连，HTTP/1.1 下触发连接重置日志，静默处理
        import os as _os
        if _os.environ.get("DFS_HTTP_LOG"):
            try:
                super().log_error(*args)
            except Exception:
                pass

    def _opts(self, qs):
        def int_opt(name, default=0):
            try:
                return int(qs.get(name, [str(default)])[0])
            except (TypeError, ValueError):
                return default

        opts = {
            "kbps": int_opt("kbps"),
            "jitter": int_opt("jitter"),
            "drop": int_opt("drop"),
            "maxconn": int_opt("maxconn"),
            "close": int_opt("close"),
            "slowpct": int_opt("slowpct"),
            "slowevery": int_opt("slowevery"),
            "slowdiv": max(2, int_opt("slowdiv", 4)),
        }
        # 慢连接判定：slowevery 优先（按连接建立序号，确定性，便于测试工作窃取）；
        # 否则按客户端端口随机派生 slowpct（同一 keep-alive 连接保持稳定）。
        conn_id = getattr(self, "_conn_id", 0)
        if opts["kbps"] and opts["slowevery"] and (conn_id % opts["slowevery"]) == 0:
            opts["kbps"] = max(1, opts["kbps"] // opts["slowdiv"])
        else:
            port = self.client_address[1] if self.client_address else conn_id
            rng = random.Random(port)
            if opts["kbps"] and opts["slowpct"] and rng.random() * 100 < opts["slowpct"]:
                opts["kbps"] = max(1, opts["kbps"] // opts["slowdiv"])
            # 该连接是否在本次响应中途断流（按端口确定性派生）
            opts["_drop_this"] = bool(
                opts["drop"] and rng.random() * 100 < opts["drop"])
        if "_drop_this" not in opts:
            port = self.client_address[1] if self.client_address else conn_id
            opts["_drop_this"] = bool(opts["drop"] and
                                      random.Random(port).random() * 100 < opts["drop"])
        return opts

    def setup(self):
        """每条 TCP 连接建立时分配一个单调递增的连接序号（确定性慢连接用）。"""
        super().setup()
        srv = self.server
        with srv._seq_lock:
            self._conn_id = srv.conn_seq
            srv.conn_seq += 1

    def _resolve(self):
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        qs = parse_qs(parsed.query)
        opts = self._opts(qs)
        mode = "range"
        self._real_name = ""
        self._no_cd = False
        if parts and parts[0] == "norange":
            mode = "no-range"
            parts = parts[1:]
        elif parts and parts[0] == "file":
            parts = parts[1:]
        elif parts and parts[0] in ("cdn", "cdn_nocd"):
            # 模拟对象存储/CDN 签名直链：路径末段是随机 token，真实文件名只由
            # Content-Disposition（cdn）或 query f（cdn_nocd）给出
            self._no_cd = parts[0] == "cdn_nocd"
            parts = parts[1:]
            name = os.path.basename(qs.get("f", [""])[0])
            self._real_name = name
            fpath = os.path.join(self.serve_dir, name)
            return mode, fpath, opts
        name = os.path.basename(parts[0]) if parts else ""
        fpath = os.path.join(self.serve_dir, name)
        return mode, fpath, opts

    def _maybe_redirect(self):
        """模拟 GitHub Release / SourceForge：原始链接 302 到随机 token 的 CDN 直链。"""
        import uuid as _uuid
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0] in ("redir", "redir_nocd") and len(parts) >= 2:
            target = "cdn" if parts[0] == "redir" else "cdn_nocd"
            name = os.path.basename(parts[1])
            loc = f"/{target}/{_uuid.uuid4().hex}?f={name}"
            self.send_response(302)
            self.send_header("Location", loc)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return True
        return False

    def _cd_name(self, fpath: str) -> str:
        return self._real_name or os.path.basename(fpath)

    def _extra_headers(self, opts):
        if opts.get("close"):
            self.send_header("Connection", "close")
            self.close_connection = True

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()

    def do_HEAD(self):
        if self._maybe_redirect():
            return
        mode, fpath, opts = self._resolve()
        if not os.path.exists(fpath):
            self.send_error(404)
            return
        size = os.path.getsize(fpath)
        self.send_response(200)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Type", _content_type(fpath))
        self.send_header("Accept-Ranges", "bytes")
        if not self._no_cd:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{self._cd_name(fpath)}"')
        self._extra_headers(opts)
        self.end_headers()

    def _server_semaphore(self, n: int):
        srv = self.server
        with getattr(srv, "_sem_lock"):
            sem = srv.conn_sems.get(n)
            if sem is None:
                sem = threading.Semaphore(n)
                srv.conn_sems[n] = sem
        return sem

    def do_GET(self):
        if self._maybe_redirect():
            return
        mode, fpath, opts = self._resolve()
        if not os.path.exists(fpath):
            self.send_error(404)
            return
        with open(fpath, "rb") as f:
            data = f.read()
        total = len(data)
        start, end = 0, total - 1
        range_header = self.headers.get("Range", "")
        m = RANGE_RE.search(range_header) if range_header else None
        use_range = mode == "range" and m is not None
        if use_range:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else total - 1
            end = min(end, total - 1)
            if start > end or start >= total:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{total}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(total))
        self.send_header("Content-Type", _content_type(fpath))
        if not self._no_cd:
            self.send_header("Content-Disposition",
                             f'attachment; filename="{self._cd_name(fpath)}"')
        self._extra_headers(opts)
        self.end_headers()

        # 断流：在发送到 10%~70% 处强制截断（Content-Length 仍声明完整长度，
        # 客户端据此识别为不完整连接并报错重试）
        cutoff = end + 1
        if opts["_drop_this"] and end > start:
            cutoff = start + max(1, int((end - start) * random.uniform(0.1, 0.7)))

        sem = None
        if opts["maxconn"]:
            sem = self._server_semaphore(opts["maxconn"])
            sem.acquire()
        chunk = 32 * 1024
        pos = start
        try:
            while pos <= end:
                n = min(chunk, end - pos + 1)
                self.wfile.write(data[pos : pos + n])
                self.wfile.flush()
                pos += n
                if pos >= cutoff:
                    # 模拟中途断流：直接关闭底层连接
                    try:
                        self.wfile.close()
                    except Exception:
                        pass
                    return
                if opts["jitter"]:
                    time.sleep(random.uniform(0, opts["jitter"] / 1000.0))
                if opts["kbps"]:
                    time.sleep(n / (opts["kbps"] * 1024))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            if sem is not None:
                sem.release()


def make_server(serve_dir: str, port: int = 0):
    """返回 (httpd, url_prefix)。port=0 时由系统分配端口。"""

    class Handler(_Handler):
        pass

    class _QuietServer(ThreadingHTTPServer):
        conn_sems: dict = {}
        _sem_lock = threading.Lock()
        conn_seq = 0
        _seq_lock = threading.Lock()

        def handle_error(self, request, client_address):
            import os as _os
            if _os.environ.get("DFS_HTTP_LOG"):
                super().handle_error(request, client_address)

    Handler.serve_dir = serve_dir
    httpd = _QuietServer(("127.0.0.1", port), Handler)
    actual_port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{actual_port}"
