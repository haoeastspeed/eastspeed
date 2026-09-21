# -*- coding: utf-8 -*-
"""引擎异常类型。"""


class DownloadError(Exception):
    """下载相关错误基类。"""


class ProbeError(DownloadError):
    """无法探测文件信息（链接无效、服务器错误等）。"""


class RangeUnsupported(DownloadError):
    """服务器实际不支持 Range 请求，需要降级为单线程重下。"""


class FatalHttpError(DownloadError):
    """不可重试的 HTTP 错误（4xx 鉴权/资源问题等）。"""


class PauseRequested(Exception):
    """内部控制信号：用户请求暂停，worker 立即退出。"""


class RetryExhausted(DownloadError):
    """重试次数耗尽。"""
