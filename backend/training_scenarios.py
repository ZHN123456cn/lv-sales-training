"""
LV 销售培训模拟场景配置。

每个场景定义一种顾客画像和进店情境，用于客户 Agent 角色扮演。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    icon: str
    description: str
    # 注入到客户 Agent 的场景描述（会作为 system 上下文）
    customer_profile: str
    # 顾客开场行为描述
    opening_behavior: str


SCENARIOS: list[Scenario] = [
    Scenario(
        id="first_time",
        title="首次进店顾客",
        icon="🛍️",
        description="第一次走进 LV 门店的顾客，对品牌有认知但不深入，想找日常使用的包。",
        customer_profile=(
            "你是一位 28 岁的职场女性，今天第一次走进 LV 门店。"
            "你月薪 1.5 万，想找一个可以通勤用的包，预算 1-2 万。"
            "你对 LV 的了解仅限于知道它是名牌，看过别人背经典老花款。"
            "你有些拘谨，不太敢主动问价格，担心被推销。"
            "你希望销售顾问能亲切自然地接待你，不要一上来就强推贵的产品。"
        ),
        opening_behavior=(
            "你推门走进门店，环顾四周，有些拘谨地看了看展示的包，"
            "然后小声说：'你好，我就随便看看...'"
        ),
    ),
    Scenario(
        id="gift_buyer",
        title="送礼顾客",
        icon="🎁",
        description="要给妻子/女友买生日礼物的顾客，不确定选什么款式，需要专业建议。",
        customer_profile=(
            "你是一位 35 岁的男性，今天来 LV 给妻子买生日礼物。"
            "你们结婚 8 年，妻子一直很喜欢 LV 但自己舍不得买。"
            "你预算 2-3 万，希望选一款她会喜欢且实用的包。"
            "你对 LV 产品了解不多，需要销售顾问帮你推荐。"
            "你会关心款式是否适合 35 岁女性、包装是否精美、有没有贺卡服务。"
        ),
        opening_behavior=(
            "你走进门店，径直走向销售顾问，说：'你好，我想给我太太买个生日礼物，"
            "你们有什么推荐吗？'"
        ),
    ),
    Scenario(
        id="lv_expert",
        title="LV 资深顾客",
        icon="👑",
        description="已经拥有多个 LV 产品的老顾客，对品牌非常了解，专门来看看新款和限量款。",
        customer_profile=(
            "你是一位 30 岁的时尚达人，已经拥有 Speedy、Neverfull、Alma 等 5 个 LV 包。"
            "你对 LV 的系列、材质、限量款非常了解，能辨别真假和年份。"
            "今天你专门来看最新款 Capucines 和限量系列。"
            "你希望销售顾问也很专业，如果你提到的产品对方不知道，你会质疑其专业性。"
            "你会主动展示知识，考验销售顾问的产品功底。"
        ),
        opening_behavior=(
            "你走进门店，直接问：'你们这次 Capucines 新出的配色到货了吗？"
            "就是那个蟒蛇皮手柄的限定款。'"
        ),
    ),
    Scenario(
        id="high_net_worth",
        title="高净值客户",
        icon="💎",
        description="资产丰厚的 VIP 客户，关注品质和独特性，希望被重视，需要 VIP 级服务。",
        customer_profile=(
            "你是一位 45 岁的企业高管，穿着考究，佩戴百达翡丽手表。"
            "你是 LV 的 VIP 客户，每年消费 50 万以上。"
            "你不关注价格，只关注独特性、稀缺性和私人定制服务。"
            "你希望销售顾问能叫出你的姓氏，提供 VIP 室服务，"
            "推荐限量款或定制款，而不是大路货。"
            "如果被当作普通顾客接待，你会非常不满。"
        ),
        opening_behavior=(
            "你走进门店，气场强大，淡淡说：'我想看一些别人没有的款式。'"
        ),
    ),
    Scenario(
        id="hesitant",
        title="犹豫型顾客",
        icon="🤔",
        description="很喜欢某款产品但下不了决心，反复纠结，需要被专业说服。",
        customer_profile=(
            "你是一位 32 岁的白领，已经第 3 次来看同一款 LV 包了。"
            "你很喜欢这款包，但总觉得价格偏高，一直在纠结。"
            "你会说'再想想'、'有点贵'、'下次再来'，其实心里已经很想要了。"
            "你希望销售顾问能帮你下定决心，但不要用逼单的方式。"
            "如果对方能讲清楚产品价值和保值性，你会更容易被说服。"
        ),
        opening_behavior=(
            "你走进门店，直接走到之前看中的那款包前，说：'我又来了..."
            "这个包我看了好几次了，但还是有点下不去手...'"
        ),
    ),
]


def get_scenario(scenario_id: str) -> Scenario | None:
    """根据 ID 获取场景。"""
    for s in SCENARIOS:
        if s.id == scenario_id:
            return s
    return None


def list_scenarios() -> list[dict]:
    """返回场景列表（用于前端展示）。"""
    return [
        {
            "id": s.id,
            "title": s.title,
            "icon": s.icon,
            "description": s.description,
        }
        for s in SCENARIOS
    ]


def build_customer_system_prompt(scenario: Scenario, student_name: str) -> str:
    """构建客户 Agent 的完整系统提示（身份 + 场景）。"""
    return (
        f"你现在要扮演 LV 门店的一位真实顾客，与销售顾问 {student_name} 对话。\n\n"
        f"【场景设定】{scenario.title}\n"
        f"{scenario.description}\n\n"
        f"【顾客画像】\n{scenario.customer_profile}\n\n"
        f"【你的开场行为】\n{scenario.opening_behavior}\n\n"
        "请严格按照以上设定扮演顾客，自然真实地与销售顾问对话。"
        "记住：你是顾客，不是 AI，不要出戏。"
    )


def build_scenario_brief(scenario: Scenario) -> str:
    """场景正文：每轮注入 system prompt 的 `<scenario>` 段内容。

    只含"这一场是谁、预算多少、知识水平如何"这类**稳定事实**；
    不含「开场行为」——那句话只在第 1 轮有意义，每轮都注入会让顾客反复打招呼。
    """
    return (
        f"【场景】{scenario.title}\n"
        f"{scenario.description}\n\n"
        f"【顾客画像】\n{scenario.customer_profile}"
    )


def build_evaluation_prompt(
    scenario: Scenario,
    student_name: str,
    student_id: str,
    dialogue_text: str,
) -> str:
    """教练评价请求正文（生产 /evaluate 与质检复现必须逐字一致）。

    抽成函数的原因：这段文本此前内联在路由里，质检侧只能手抄一份。
    手抄的副本一定会漂移——改了一边忘了另一边，两边测的就不是同一件事。
    现在生产调它、质检生成它、回归脚本比对它。
    """
    return (
        f"请你作为 LV 销售培训教练，评价以下销售顾问的模拟演练表现。\n\n"
        f"【学员信息】\n"
        f"姓名：{student_name}\n"
        f"学号：{student_id}\n"
        f"模拟场景：{scenario.title} - {scenario.description}\n\n"
        f"【完整对话记录】\n{dialogue_text}\n\n"
        "请按照你的评价体系，给出结构化评估报告（包含各维度打分、亮点、待改进、示范话术、总结和下一步练习建议）。\n"
        "直接输出评价报告，不要包含任何思考过程。"
    )


# ---------------------------------------------------------------------------
# 报告自洽性：总分按维度之和重算
# ---------------------------------------------------------------------------

# 维度行：`| 接待礼仪 | 5/10 | 简评 |`（简评可空，也可能没有第三列）
_DIM_ROW = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(\d+(?:\.\d+)?)\s*/\s*10\s*\|", re.M)
# 总分：`### 综合得分：24 / 50 分`
_TOTAL = re.compile(r"(综合得分\s*[:：]\s*)(\d+(?:\.\d+)?)(\s*/\s*)(\d+(?:\.\d+)?)")


def reconcile_report_total(text: str, expected_dims: list[str]) -> tuple[str, dict]:
    """让「综合得分」等于各维度之和，并在改写处标注。

    为什么必须做（F-20261116-04）：
        实测出现过「综合得分 24 / 50」而五维是 5+3+3+4+5=20 的报告。
        格式完全正确、每个维度都写了，唯独总分是模型另算的一份。
        这种错误不会触发任何程序异常，只有把两个数字相加才看得出——
        而学员会照着错的分数决定下一步练什么。

    原则：**解析不完整就原样返回，绝不猜**。
        宁可保留一个可疑的总分（质检器的 T-TOTAL-01 会抓），
        也不能因为正则没匹配全就把报告改坏。

    Returns:
        (新文本, info)。info.reason 取值：
        - `ok`            总分与维度之和一致，未改动
        - `fixed`         不一致，已按维度之和改写
        - `no_total`      没找到「综合得分」
        - `no_dims`       没解析到任何维度分
        - `partial`       维度数与预期不符（可能缺项），不改
        - `out_of_range`  维度之和超过分母或为负，不改
    """
    total_match = _TOTAL.search(text)
    if total_match is None:
        return text, {"reason": "no_total"}

    dims: dict[str, float] = {}
    for name, score in _DIM_ROW.findall(text):
        name = name.strip()
        # 表头与分隔行也会命中 `| xxx | n/10 |` 形态的可能性极低，
        # 但仍要求维度名在预期集合里，避免把别的表格算进来。
        if name in expected_dims:
            dims[name] = float(score)

    if not dims:
        return text, {"reason": "no_dims"}
    if len(dims) != len(expected_dims):
        return text, {"reason": "partial", "found": sorted(dims), "dims": dims}

    reported = float(total_match.group(2))
    total_max = float(total_match.group(4))
    computed = sum(dims.values())

    if computed <= 0 or computed > total_max:
        return text, {"reason": "out_of_range", "computed": computed, "max": total_max}

    info = {
        "reason": "ok",
        "reported": reported,
        "computed": computed,
        "dims": dims,
    }
    if abs(reported - computed) < 1e-6:
        return text, info

    # 改写：只替换分数本身，保留原有分隔与单位写法。
    new_total = f"{computed:g}"
    replaced = (
        text[: total_match.start(2)]
        + new_total
        + text[total_match.end(2) :]
    )
    # 在前面那行（"### 综合得分…"所在行）末尾补一句说明，避免看起来像模型自己算的。
    line_start = replaced.rfind("\n", 0, total_match.start(1)) + 1
    line_end = replaced.find("\n", total_match.start(1))
    line_end = len(replaced) if line_end < 0 else line_end
    note = f"（总分已按各维度之和校正，原报告写作 {reported:g}）"
    replaced = replaced[:line_end] + note + replaced[line_end:]

    info["reason"] = "fixed"
    return replaced, info


# 教练报告的 5 个维度。这里是**唯一**允许出现维度名列表的地方：
# 路由用它做总分校正，测试用它和 workspace_coach/IDENTITY.md 比对。
# 别在别处再抄一份——此前口头约定的"产品推荐/品牌知识/成交"就是这么错的，
# 结果报告明明合格却被判 3/5。
COACH_EVALUATION_DIMENSIONS: list[str] = [
    "接待礼仪",
    "需求挖掘",
    "产品知识",
    "销售技巧",
    "沟通表达",
]
