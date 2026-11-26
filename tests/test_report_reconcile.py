"""总分校正（F-20261116-04）。

实测缺陷：教练报告写「综合得分 24 / 50」，五个维度却是 5+3+3+4+5=20。
格式全对、每个维度都写了，只有相加才看得出——学员会照着错分决定练什么。
处置：总分是派生值，由代码按维度之和重算，并在改写处标注。
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.training_scenarios import (
    COACH_EVALUATION_DIMENSIONS,
    reconcile_report_total,
)

DIMS = COACH_EVALUATION_DIMENSIONS
PROJECT_ROOT = Path(__file__).resolve().parent.parent

SAMPLE = """## 演练评估报告

### 综合得分：24 / 50 分

| 评价维度 | 得分 | 简评 |
|---------|------|------|
| 接待礼仪 | 5/10 | 有基本问候 |
| 需求挖掘 | 3/10 | 未主动询问 |
| 产品知识 | 3/10 | 缺少系列说明 |
| 销售技巧 | 4/10 | 未回应预算异议 |
| 沟通表达 | 5/10 | 语言简洁 |
"""


def test_mismatch_is_fixed():
    text, info = reconcile_report_total(SAMPLE, DIMS)
    assert info["reason"] == "fixed"
    assert info["reported"] == 24
    assert info["computed"] == 20
    assert "综合得分：20 / 50" in text
    assert "总分已按各维度之和校正" in text
    assert "原报告写作 24" in text


def test_consistent_report_is_untouched():
    ok = SAMPLE.replace("24 / 50", "20 / 50")
    text, info = reconcile_report_total(ok, DIMS)
    assert info["reason"] == "ok"
    assert text == ok


def test_missing_total_is_left_alone():
    text = SAMPLE.replace("### 综合得分：24 / 50 分", "")
    out, info = reconcile_report_total(text, DIMS)
    assert info["reason"] == "no_total"
    assert out == text


def test_partial_dimensions_not_touched():
    """少一个维度时不能"补算"——那会把缺项说成 0 分。"""
    partial = SAMPLE.replace("| 沟通表达 | 5/10 | 语言简洁 |\n", "")
    out, info = reconcile_report_total(partial, DIMS)
    assert info["reason"] == "partial"
    assert out == partial


def test_unknown_dimension_names_ignored():
    """表头或别的表格里的 n/10 不能被当成维度分。"""
    polluted = SAMPLE.replace("| 评价维度 | 得分 | 简评 |", "| 评价维度 | 得分 | 简评 |\n| 其它表格 | 9/10 | 不算 |")
    _, info = reconcile_report_total(polluted, DIMS)
    # 维度仍是 5 个（"其它表格"不在预期集合里），总分照常校正
    assert info["reason"] == "fixed"
    assert info["computed"] == 20


def test_no_dimension_rows_is_left_alone():
    text = "### 综合得分：24 / 50 分\n\n没有表格。"
    out, info = reconcile_report_total(text, DIMS)
    assert info["reason"] == "no_dims"
    assert out == text


def test_sum_exceeding_max_is_left_alone():
    """维度之和超过分母说明解析错位，不能把报告改成一个更离谱的数。"""
    bad = SAMPLE.replace("| 接待礼仪 | 5/10 | 有基本问候 |", "| 接待礼仪 | 50/10 | 夸张 |")
    out, info = reconcile_report_total(bad, DIMS)
    assert info["reason"] == "out_of_range"
    assert out == bad


def test_dimension_list_matches_identity_md():
    """维度名必须与 workspace_coach/IDENTITY.md 逐字一致。

    这条测试是防止"改了一边忘了另一边"——早期按口头约定写成
    「产品推荐/品牌知识/成交」，导致合格报告被判 3/5。
    """
    identity = (PROJECT_ROOT / "workspace_coach" / "IDENTITY.md").read_text(encoding="utf-8")
    found = re.findall(r"^###\s*\d+\.\s*(.+?)\s*$", identity, re.M)
    assert found == DIMS, f"IDENTITY.md 声明 {found}，代码里是 {DIMS}"
