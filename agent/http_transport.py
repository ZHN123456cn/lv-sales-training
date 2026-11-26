"""HTTP 传输层：给 OpenAI SDK 注入一个不会在解码阶段崩溃的 http client。

为什么需要这个模块（2026-11 本机实测，别再删）：
    openai 3.6.0 走 httpx2 2.12.0；httpx2 的 BrotliDecoder 这样解压：

        self._decompress = self.decompressor.process
        decompressed = self._decompress(data, output_buffer_limit=output_buffer_limit)

    但本机 brotli 1.0.9 的 ``Decompressor.process(data)`` 不接受任何关键字参数，
    于是只要响应带 ``Content-Encoding: br`` 就抛：

        TypeError: process() takes no keyword arguments

    服务端只对大响应启用 br 压缩，所以症状很有欺骗性：
        - 短请求（几十 token）一切正常
        - 长输出（报告、清单）→ 非流式被 SDK 包成 APIConnectionError，看着像网络问题
        - 流式 → TypeError 直接冒出来，看着像 SDK bug

    结论：这不是业务代码 bug，是依赖组合不配套。此处不声明 br，只声明
    gzip/deflate/zstd，用一点带宽换"请求必定能解码"。
    等 brotli / httpx2 版本对上了，把 ACCEPT_ENCODING 改回 None 即可。

单独成模块的原因：这是环境适配，不是业务逻辑；放在 llm.py 里会让人
以为它跟对话流程有关，下次重构容易被顺手删掉。
"""

from __future__ import annotations

from typing import Any

# 不包含 br —— 见模块 docstring。identity 本来就不该声明。
SAFE_ACCEPT_ENCODING = "gzip, deflate, zstd"

# openai 3.6.0 同时接受 httpx2.Client 和 httpx.Client，按它的实际实现挑。
_HTTP_LIBS = ("httpx2", "httpx")


def _resolve_http_module() -> Any | None:
    """按 openai 实际使用的库返回 httpx 模块；都不可用则返回 None。"""
    for name in _HTTP_LIBS:
        try:
            return __import__(name)
        except ImportError:
            continue
    return None


def build_http_client(timeout: float | None = None) -> Any | None:
    """构造一个不请求 brotli 的 http client。

    Args:
        timeout: 传输层超时秒数；与 LLMConfig.timeout 保持一致。

    Returns:
        httpx/httpx2 的 Client；环境不支持时返回 None，让 SDK 用默认 client
        （此时长输出仍可能因 br 解码失败而断，但至少不会因为适配层写死而
        把整个应用打挂——降级优于崩溃）。
    """
    mod = _resolve_http_module()
    if mod is None:
        return None

    kwargs: dict[str, Any] = {"headers": {"accept-encoding": SAFE_ACCEPT_ENCODING}}
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        return mod.Client(**kwargs)
    except Exception:
        # 构造失败不该阻断对话，交回 SDK 默认实现。
        return None
