# -*- coding: utf-8 -*-
"""下载完成后的校验和（MD5/SHA-1/SHA-256/SHA-512）计算与比对。"""
from __future__ import annotations

import hashlib

SUPPORTED = ("md5", "sha1", "sha256", "sha512")
ALIASES = {
    "md5": "md5",
    "sha1": "sha1", "sha-1": "sha1",
    "sha256": "sha256", "sha-256": "sha256",
    "sha512": "sha512", "sha-512": "sha512",
}


def normalize_algo(name: str | None) -> str:
    if not name:
        return ""
    return ALIASES.get(name.strip().lower(), "")


def normalize_hex(value: str | None) -> str:
    return (value or "").strip().lower().replace(" ", "")


def hash_file(path: str, algo: str, chunk: int = 1 << 20,
              should_stop=None) -> str:
    """流式计算文件摘要，避免一次性读入内存。

    should_stop: 可选的无参回调，返回 True 时中止（抛出 InterruptedError）。
    """
    name = normalize_algo(algo)
    if name not in SUPPORTED:
        raise ValueError(f"不支持的校验算法: {algo}")
    h = hashlib.new(name)
    with open(path, "rb") as f:
        while True:
            if should_stop is not None and should_stop():
                raise InterruptedError("校验被中止")
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def verify_file(path: str, algo: str, expected: str,
                should_stop=None) -> tuple[bool, str]:
    """返回 (是否匹配, 实际摘要)。expected 为空时只计算不比对。"""
    actual = hash_file(path, algo, should_stop=should_stop)
    exp = normalize_hex(expected)
    if not exp:
        return True, actual  # 未提供期望值：仅产出实际摘要
    return actual == exp, actual
