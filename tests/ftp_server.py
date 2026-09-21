# -*- coding: utf-8 -*-
"""测试用 FTP / FTPS 服务器（pyftpdlib），后台线程运行，支持 REST/SIZE。"""
import datetime
import os
import threading

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import ThreadedFTPServer


def make_cert(path: str) -> str:
    """用 cryptography 生成自签证书（key+cert 合并 PEM），供 FTPS 测试。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow() - datetime.timedelta(days=1))
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    with open(path, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption()))
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return path


class FtpTestServer:
    def __init__(self, root: str, tls_cert: str | None = None,
                 user: str | None = None, password: str | None = None):
        authorizer = DummyAuthorizer()
        if user:
            authorizer.add_user(user, password or "", root, perm="elradfmwMT")
        else:
            authorizer.add_anonymous(root, perm="elradfmwMT")

        if tls_cert:
            from pyftpdlib.handlers import TLS_FTPHandler
            handler = TLS_FTPHandler
            handler.certfile = tls_cert
            handler.tls_control_required = True
            handler.tls_data_required = True
        else:
            handler = FTPHandler
        handler.authorizer = authorizer
        handler.banner = "pydownloader test ftp"

        self.server = ThreadedFTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.socket.getsockname()[1]
        self._thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"timeout": 0.2},
            daemon=True, name="test-ftp")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self.server.close_all()
        return False
