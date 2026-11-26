"""三 Agent 协作的 LangGraph 编排：顾客模拟 -> 销售训练 -> 教练评估。

对照手写版 backend/routers/training.py 的流程：
    start_training()  -> customer_opening 节点（switch_agent customer + 生成开场）
    chat() 多轮        -> customer_reply 节点内的循环（顾客逐轮回应销售）
    evaluate()        -> coach_evaluate 节点（switch_agent coach + 评价 + 总分校正）

handoff 实现：节点通过返回 `Command(goto=下一个节点, update=状态更新)`
显式移交控制权——对应手写版 manager.switch_agent("customer"/"coach")。

关键：本图不重复任何 prompt/评价体系，全部调用 backend.training_scenarios 的
生产函数。唯一变量是"角色切换由 LangGraph 编排"，便于 A/B 对比。
"""

from __future__ import annotations

from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from ..config import AgentConfig
from ..llm import LLMClient
from ..session import Session
from .graph import LangGraphAgent


class TrainingState(TypedDict, total=False):
    scenario: Any                     # backend.training_scenarios.Scenario
    student_name: str
    student_id: str
    sales_inputs: list[str]           # 销售的话（多轮）
    dialogue: list[dict[str, str]]    # [{"role": "销售顾问"|"顾客", "content": ...}]
    customer_reply: str
    evaluation: str
    total_reconciled: str
    current_role: str                 # customer / coach
    usage: dict[str, int]


def build_training_graph(
    llm: LLMClient,
    config: AgentConfig,
    checkpointer: Any = None,
) -> Any:
    """构建三 Agent 协作图，一次 invoke 走完开场->多轮训练->评价。"""
    from backend.training_scenarios import (
        COACH_EVALUATION_DIMENSIONS,
        build_customer_system_prompt,
        build_evaluation_prompt,
        reconcile_report_total,
    )

    def make_agent(role: str) -> tuple[Session, LangGraphAgent]:
        """按角色创建 session + agent，对应 switch_agent()。"""
        session = Session(
            id=f"lg-{role}", created_at="", updated_at="",
            work_dir=f"agent:{role}", messages=[],
        )
        return session, LangGraphAgent(config, session, llm)

    def customer_opening(state: TrainingState) -> Command:
        """节点1：顾客生成开场白 -> handoff 到 customer_reply。"""
        system_prompt = build_customer_system_prompt(
            state["scenario"], state["student_name"]
        )
        response = llm.chat_blocking(messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": (
                "请开始你的角色，说出你进入门店后的第一句话。"
                "直接以顾客身份说话，不要输出任何思考过程或内心独白标签。"
            )},
        ])
        opening = ((getattr(response, "content", "") or "").strip())
        return Command(
            goto="customer_reply",
            update={
                "dialogue": [{"role": "顾客", "content": opening}],
                "current_role": "customer",
            },
        )

    def customer_reply(state: TrainingState) -> Command:
        """节点2：顾客逐轮回应销售（多轮循环） -> handoff 到 coach_evaluate。"""
        session, agent = make_agent("customer")
        try:
            for turn in state["dialogue"]:
                role = "user" if turn["role"] == "销售顾问" else "assistant"
                session.messages.append({"role": role, "content": turn["content"]})
            for sales_input in state["sales_inputs"]:
                reply = agent.run(sales_input, thread_id="lg-customer")
                state["dialogue"].append({"role": "销售顾问", "content": sales_input})
                state["dialogue"].append({"role": "顾客", "content": reply})
        finally:
            agent.close()

        return Command(
            goto="coach_evaluate",
            update={
                "dialogue": state["dialogue"],
                "current_role": "coach",
            },
        )

    def coach_evaluate(state: TrainingState) -> Command:
        """节点3：教练评价（复用生产 prompt + 总分校正） -> END。"""
        dialogue_text = "\n\n".join(
            f"{t['role']}：{t['content']}" for t in state["dialogue"]
        )
        evaluation_prompt = build_evaluation_prompt(
            state["scenario"], state["student_name"],
            state["student_id"], dialogue_text,
        )
        session, agent = make_agent("coach")
        try:
            raw = agent.run(evaluation_prompt, thread_id="lg-coach")
        finally:
            agent.close()
        fixed, total_info = reconcile_report_total(
            raw, COACH_EVALUATION_DIMENSIONS
        )
        return Command(
            goto=END,
            update={
                "evaluation": fixed,
                "total_reconciled": total_info.get("reason", ""),
                "current_role": "coach",
            },
        )

    builder = StateGraph(TrainingState)
    builder.add_node("customer_opening", customer_opening)
    builder.add_node("customer_reply", customer_reply)
    builder.add_node("coach_evaluate", coach_evaluate)
    builder.add_edge(START, "customer_opening")
    return builder.compile(checkpointer=checkpointer)


def run_training_session(
    llm: LLMClient,
    config: AgentConfig,
    scenario: Any,
    student_name: str,
    student_id: str,
    sales_inputs: list[str],
    thread_id: str = "training-default",
) -> dict[str, Any]:
    """驱动一次完整培训，返回对话与评价。便于 A/B 对比脚本调用。"""
    graph = build_training_graph(llm, config)
    initial: TrainingState = {
        "scenario": scenario,
        "student_name": student_name,
        "student_id": student_id,
        "sales_inputs": sales_inputs,
        "dialogue": [],
        "current_role": "customer",
    }
    result = graph.invoke(initial, config={"configurable": {"thread_id": thread_id}})
    return {
        "dialogue": result.get("dialogue", []),
        "evaluation": result.get("evaluation", ""),
        "total_reconciled": result.get("total_reconciled", ""),
        "turns": len(sales_inputs),
    }
