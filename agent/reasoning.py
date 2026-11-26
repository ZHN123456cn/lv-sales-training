"""
思考块过滤器：把模型输出里的 `<think>` 一类思维链从"用户可见文本"里剥掉。

为什么放在引擎（而非某个路由）：
  质检与生产必须走同一条输出路径。如果只在 `/api/chat/stream` 里过滤，
  同进程质检直接调 `AgentLoop.run()` 就永远看不到这段逻辑——要么漏报，
  要么（更糟）质检报出一个用户其实看不见的"缺陷"。剥离放在 `loop.py`
  是唯一能让生产、CLI、质检三处同时生效的位置。

为什么不能只做正则替换：
  流式输出的 token 是碎片，`<thi` / `nk>` 可能分属两次 yield。必须缓冲
  未决片段，否则要么漏掉半个标签，要么把用户的正常尖括号文本吃掉。

统计为什么必须留：
  剥干净之后，"模型不再产生思考块"和"产生了但被剥掉"在可见文本上完全
  一样。`reasoning_blocks/reasoning_chars` 用来区分这两件事——否则修完
  之后没人能证明过滤器真的在工作，而不是问题自己消失了。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# 常见思维链标签。加新标签只要往这里追加，不需要改状态机。
REASONING_TAGS: tuple[str, ...] = ("think", "thinking", "reasoning", "analysis")


@dataclass
class ReasoningStats:
    """一轮输出的剥离统计。"""

    blocks: int = 0          # 完整剥离的思考块个数
    chars: int = 0           # 被剥离的字符数（含标签本身）
    raw_chars: int = 0       # 剥离前长度
    clean_chars: int = 0     # 剥离后长度
    empty_after_strip: bool = False  # 剥完为空：模型只输出了思考，没给答复

    def to_dict(self) -> dict[str, object]:
        return {
            "blocks": self.blocks,
            "chars": self.chars,
            "raw_chars": self.raw_chars,
            "clean_chars": self.clean_chars,
            "empty_after_strip": self.empty_after_strip,
        }


class ReasoningFilter:
    """增量式思考块剥离器，可安全用于流式 token。"""

    def __init__(self, tags: tuple[str, ...] = REASONING_TAGS) -> None:
        self._tags = tuple(t.lower() for t in tags)
        self._pending = ""          # 尚未决定去留的尾巴（可能是半个标签）
        self._open_tag: str | None = None
        self.stats = ReasoningStats()

    # ------------------------------------------------------------------
    # 流式接口
    # ------------------------------------------------------------------

    def feed(self, chunk: str) -> str:
        """喂入一个碎片，返回此刻可以安全下发的文本（可能为空串）。"""
        if not chunk:
            return ""
        self.stats.raw_chars += len(chunk)
        self._pending += chunk
        out: list[str] = []

        while self._pending:
            if self._open_tag is None:
                idx = self._pending.find("<")
                if idx < 0:
                    out.append(self._pending)
                    self._pending = ""
                    break
                if idx > 0:
                    out.append(self._pending[:idx])
                    self._pending = self._pending[idx:]
                kind, tag, end = self._match_tag(self._pending)
                if kind == "open":
                    self.stats.chars += end
                    self._pending = self._pending[end:]
                    self._open_tag = tag
                    continue
                if kind == "close":
                    # 模型只吐了 `</think>`（开标签被模板吃掉）：整段丢弃
                    self.stats.chars += end
                    self._pending = self._pending[end:]
                    continue
                if kind == "partial":
                    break
                # 不是思考标签：`<` 当普通字符下发
                out.append(self._pending[0])
                self._pending = self._pending[1:]
                continue

            # 块内：一直丢到闭合标签
            closer = f"</{self._open_tag}"
            low = self._pending.lower()
            pos = low.find(closer)
            if pos < 0:
                keep = self._suffix_that_may_start(closer)
                drop = len(self._pending) - keep
                self.stats.chars += drop
                self._pending = self._pending[drop:]
                break
            gt = self._pending.find(">", pos)
            if gt < 0:
                # 闭合标签还没收全，先攒着
                self.stats.chars += pos
                self._pending = self._pending[pos:]
                break
            self.stats.chars += gt + 1
            self._pending = self._pending[gt + 1:]
            self._open_tag = None
            self.stats.blocks += 1

        text = "".join(out)
        self.stats.clean_chars += len(text)
        return text

    def flush(self) -> str:
        """输出结束时调用。返回尾部残留（未闭合的思考块直接丢弃）。"""
        rest = self._pending
        self._pending = ""
        if self._open_tag is not None:
            # 思考块没闭合：内容全部是思维链，不下发
            if rest:
                self.stats.blocks += 1
            self.stats.chars += len(rest)
            self._open_tag = None
            rest = ""
        self.stats.clean_chars += len(rest)
        return rest

    # ------------------------------------------------------------------
    # 一次性接口
    # ------------------------------------------------------------------

    def filter_all(self, text: str) -> str:
        """非流式：整段过滤。返回干净文本，统计在 `self.stats`。"""
        clean = self.feed(text or "") + self.flush()
        # 模型只输出了思考、没给答复：这是另一种缺陷，单独标出来，
        # 不要让它伪装成"回复很短"
        self.stats.empty_after_strip = bool(self.stats.raw_chars) and not clean.strip()
        return clean

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    def _match_tag(self, buf: str) -> tuple[str, str, int]:
        """判断 buf 开头是不是一个思考标签。

        返回 (kind, tag, end)：kind ∈ {"open", "close", "partial", "no"}，
        end 是标签在 buf 中占的字符数（partial/no 时为 0）。
        """
        low = buf.lower()
        for tag in self._tags:
            for prefix, kind in ((f"<{tag}", "open"), (f"</{tag}", "close")):
                if not low.startswith(prefix):
                    if prefix.startswith(low):
                        return "partial", tag, 0
                    continue
                after = len(prefix)
                if len(buf) == after:
                    return "partial", tag, 0
                nxt = buf[after]
                if nxt == ">":
                    return kind, tag, after + 1
                if nxt in " \t\r\n":
                    gt = buf.find(">", after)
                    if gt < 0:
                        return "partial", tag, 0
                    return kind, tag, gt + 1
                # `<thinkx` 之类：不是这个标签
                continue
        return "no", "", 0

    def _suffix_that_may_start(self, token: str) -> int:
        """buf 结尾可能是一个 token 的开头时，保留几位先不下发。"""
        low = self._pending.lower()
        for size in range(min(len(token), len(low)), 0, -1):
            if low.endswith(token[:size]):
                return size
        return 0


def strip_reasoning(text: str) -> tuple[str, ReasoningStats]:
    """便捷函数：整段剥离。<think> 泄漏修复的公共入口。"""
    f = ReasoningFilter()
    clean = f.filter_all(text)
    return clean, f.stats
