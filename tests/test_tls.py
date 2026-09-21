# -*- coding: utf-8 -*-
"""TLS 信任策略测试：自签 HTTPS 服务器 + 系统证书库/忽略校验开关。"""
import datetime
import functools
import http.server
import os
import ssl
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

import requests

from core import tls
from core.config import Settings
from core.errors import ProbeError
from core.probe import build_session, probe, session_kwargs

PAYLOAD = b"hello-internal-tls-1234567890"


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(PAYLOAD)))
        self.end_headers()
        self.wfile.write(PAYLOAD)

    def log_message(self, *a):
        pass


def _make_self_signed(d):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subj = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(subj).issuer_name(subj)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256()))
    cp = os.path.join(d, "cert.pem")
    kp = os.path.join(d, "key.pem")
    with open(cp, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(kp, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
    return cp, kp


def _serve_https(d):
    cp, kp = _make_self_signed(d)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    # truststore 注入后 ssl.SSLContext 面向客户端，服务端需用原生上下文
    ctx = tls.native_server_context()
    ctx.load_cert_chain(cp, kp)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"https://127.0.0.1:{httpd.server_address[1]}/data"


class TLSTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="dfs-tls-")
        cls.srv, cls.url = _serve_https(cls.d)

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_truststore_available(self):
        # 装了 truststore 即应能拿到基于系统证书库的客户端上下文
        self.assertIsNotNone(tls.system_ssl_context())

    def test_self_signed_rejected_by_default_with_hint(self):
        s = Settings(verify_ssl=True)
        with self.assertRaises(ProbeError) as cm:
            probe(self.url, s)
        self.assertTrue(tls.is_certificate_error(cm.exception),
                        f"应识别为证书错误: {cm.exception}")
        self.assertIn("证书", str(cm.exception))

    def test_insecure_switch_downloads(self):
        s = Settings(verify_ssl=False)
        sess = build_session(s)
        try:
            r = sess.get(self.url, **session_kwargs(s))
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, PAYLOAD)
        finally:
            sess.close()

    def test_plain_verify_false_requests(self):
        # 不经过会话工厂，直接验证 verify=False 能完成自签握手
        r = requests.get(self.url, verify=False, timeout=10)
        self.assertEqual(r.content, PAYLOAD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
