"""http_transport 单测：锁死「不声明 br」这条环境适配规则。

背景见 agent/http_transport.py 的 docstring。这些断言的意义是：
如果哪天有人为了"省带宽"把 br 加回去，或者改动了 client 构造方式，
测试会直接红，而不是等到用户点评价时才发现报告出不来。
"""

from __future__ import annotations

from agent.http_transport import SAFE_ACCEPT_ENCODING, build_http_client


def test_accept_encoding_excludes_brotli():
    """br 必须不在声明里——本机 brotli 1.0.9 的 process() 不接受 output_buffer_limit。"""
    encodings = [e.strip().lower() for e in SAFE_ACCEPT_ENCODING.split(",")]
    assert "br" not in encodings
    assert "brotli" not in encodings


def test_accept_encoding_keeps_common_alternatives():
    """去掉 br 之后仍要有可用压缩，否则等于放弃全部压缩。"""
    encodings = [e.strip().lower() for e in SAFE_ACCEPT_ENCODING.split(",")]
    assert "gzip" in encodings


def test_build_http_client_sets_header():
    client = build_http_client(timeout=7)
    assert client is not None, "环境里应有 httpx 或 httpx2"
    got = client.headers.get("accept-encoding", "")
    assert "br" not in [e.strip().lower() for e in got.split(",")]
    assert "gzip" in got


def test_build_http_client_applies_timeout():
    client = build_http_client(timeout=33)
    assert client is not None
    assert float(client.timeout.read) == 33.0


def test_build_http_client_none_timeout_is_allowed():
    """timeout=None 时不应崩，交给 SDK 决定。"""
    client = build_http_client(timeout=None)
    assert client is not None
