"""思考块过滤器的行为约束（F-20261116-02）。

这组用例存在的理由：过滤器最危险的失效方式不是"漏剥"，而是**吃正文**。
流式 token 会被拆成碎片（`<thi` / `nk>`），任何按 token 独立做正则的实现
都会把用户的正常尖括号文本吃掉或把标签漏出去。所以下面的用例刻意覆盖
"逐字符切分"——只要逐字符通过，任何更粗的切分方式都会通过。
"""

from __future__ import annotations

import pytest

from agent.reasoning import ReasoningFilter, strip_reasoning


def _stream_by(text: str, size: int) -> tuple[str, int]:
    f = ReasoningFilter()
    out = "".join(f.feed(text[i:i + size]) for i in range(0, len(text), size))
    return out + f.flush(), f.stats.blocks


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<think>abc</think>你好", "你好"),
        ("</think>你好", "你好"),                       # 开标签被模板吃掉
        ("<think>abc", ""),                             # 未闭合：全是思维链
        ("价格 <1000 元", "价格 <1000 元"),              # 普通尖括号不能吃
        ("<THINK>abc</Think>你好", "你好"),              # 大小写
        ('<think foo="1">abc</think>你好', "你好"),      # 带属性
        ("<think>a</think>你好<think>b</think>再见", "你好再见"),
        ("<thinking>a</thinking>你好", "你好"),          # think 是 thinking 的前缀
    ],
)
def test_strip_reasoning_whole_text(raw: str, expected: str) -> None:
    clean, _ = strip_reasoning(raw)
    assert clean == expected


@pytest.mark.parametrize("size", [1, 2, 3, 5, 97])
def test_streaming_matches_whole_text(size: int) -> None:
    """任意切分粒度下，流式结果必须与整段结果一致。"""
    raw = "（低头）<think>让我想想怎么说得像顾客</think>这款多少钱？"
    expected, _ = strip_reasoning(raw)
    got, blocks = _stream_by(raw, size)
    assert got == expected == "（低头）这款多少钱？"
    assert blocks == 1


def test_streaming_keeps_normal_angle_brackets_split_across_tokens() -> None:
    """`<` 和 `1000` 分属两个 token 时，尖括号必须原样下发。"""
    got, _ = _stream_by("价格 <1000 元", 1)
    assert got == "价格 <1000 元"


def test_partial_tag_at_end_is_delivered_not_swallowed() -> None:
    """流结束时只收到 `<thi`：这不是标签，是正文，必须下发。"""
    got, _ = _stream_by("价格 <thi", 1)
    assert got == "价格 <thi"


def test_stats_distinguish_stripped_from_absent() -> None:
    """剥干净之后，"产生了但被剥掉"和"根本没产生"在文本上一样，靠 stats 区分。"""
    _, stripped = strip_reasoning("<think>a</think>你好")
    _, absent = strip_reasoning("你好")
    assert stripped.blocks == 1 and stripped.chars > 0
    assert absent.blocks == 0 and absent.chars == 0


def test_empty_after_strip_is_flagged() -> None:
    """只输出思考、没给答复：必须可识别，不能伪装成"回复很短"。"""
    clean, stats = strip_reasoning("<think>只有思考</think>")
    assert clean.strip() == ""
    assert stats.empty_after_strip is True
