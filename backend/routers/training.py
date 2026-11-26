"""
培训路由：模拟客户 + 教练评价。

GET  /api/training/scenarios           列出可用场景
POST /api/training/start               开始培训（学生信息 + 场景）
POST /api/training/evaluate            结束演练，教练评价
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent.reasoning import strip_reasoning
from agent.session import Session, SessionManager

from ..core.agent_manager import AgentManager
from ..dependencies import get_agent_manager, get_session_manager
from ..training_scenarios import (
    COACH_EVALUATION_DIMENSIONS,
    build_customer_system_prompt,
    build_evaluation_prompt,
    build_scenario_brief,
    get_scenario,
    list_scenarios,
    reconcile_report_total,
)

router = APIRouter(prefix="/api/training", tags=["training"])


def _strip_think_tags(text: str) -> str:
    """移除 LLM 输出中的思考块。

    实现移到 `agent/reasoning.py`：路由层原本自己写了一份正则，而引擎层
    处理流式碎片必须用状态机，两份实现迟早不一致。这里只保留函数名兼容
    老调用点。
    """
    clean, _ = strip_reasoning(text or "")
    return clean.strip()


class TrainingStartRequest(BaseModel):
    student_name: str = Field(..., min_length=1, description="学生姓名")
    student_id: str = Field(..., min_length=1, description="学号")
    scenario_id: str = Field(..., min_length=1, description="场景 ID")


class EvaluateRequest(BaseModel):
    session_id: str = Field(..., min_length=1, description="演练 Session ID")
    student_name: str = Field(..., min_length=1, description="学生姓名")
    student_id: str = Field(..., min_length=1, description="学号")
    scenario_id: str = Field(..., min_length=1, description="场景 ID")


@router.get("/scenarios")
async def scenarios():
    """列出所有可用模拟场景。"""
    return {"scenarios": list_scenarios()}


@router.post("/start")
async def start_training(
    req: TrainingStartRequest,
    manager: AgentManager = Depends(get_agent_manager),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """
    开始培训：
    1. 切换到客户 Agent
    2. 创建新 Session
    3. 根据场景生成客户开场消息
    4. 返回 session_id 和开场内容
    """
    scenario = get_scenario(req.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"场景不存在：{req.scenario_id}")

    # 切换到客户 Agent
    manager.switch_agent("customer")

    # 创建新 Session，work_dir 中标记 agent 类型（供 chat 接口恢复上下文）
    # scenario_id 必须落进 session：后续每轮重建 system prompt 时，
    # 只能从 session 取"这场是哪个场景"（F-20261116-01）。
    session = session_mgr.new_session(
        work_dir="agent:customer",
        scenario_id=scenario.id,
    )

    # 场景正文登记进 config 映射，供每一轮的 <scenario> 段取用。
    # 在这里登记而不是只在下面拼 system_prompt：后者只在生成开场白时用一次。
    manager.register_scenario_prompt(scenario.id, build_scenario_brief(scenario))

    # 构建客户系统提示
    system_prompt = build_customer_system_prompt(scenario, req.student_name)

    # 用 LLM 生成客户开场
    try:
        response = manager.llm.chat_blocking(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": (
                    "请开始你的角色，说出你进入门店后的第一句话。"
                    "直接以顾客身份说话，不要输出任何思考过程或内心独白标签。"
                    "可以用括号描述你的动作、神态和心理状态。"
                )},
            ],
        )
        opening = _strip_think_tags(response.content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"生成开场失败：{exc}")

    # 保存到 session（客户消息作为 assistant 角色）
    session.messages.append({"role": "assistant", "content": opening})
    session.touch()
    session_mgr.save_session(session)

    return {
        "session_id": session.id,
        "opening_message": opening,
        "scenario": {
            "id": scenario.id,
            "title": scenario.title,
            "icon": scenario.icon,
        },
    }


@router.post("/evaluate")
async def evaluate(
    req: EvaluateRequest,
    manager: AgentManager = Depends(get_agent_manager),
    session_mgr: SessionManager = Depends(get_session_manager),
):
    """
    结束演练并触发教练评价：
    1. 加载对话历史
    2. 切换到教练 Agent
    3. 发送完整对话给教练 Agent 进行评价
    4. 返回结构化评价报告
    """
    scenario = get_scenario(req.scenario_id)
    if scenario is None:
        raise HTTPException(status_code=404, detail=f"场景不存在：{req.scenario_id}")

    try:
        session = session_mgr.load_session(req.session_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"Session 不存在：{req.session_id}")

    # 只取 user + assistant 的文本对话（过滤工具调用）
    dialogue_lines = []
    for msg in session.messages:
        role = msg.get("role")
        content = msg.get("content", "")
        if not content or role not in ("user", "assistant"):
            continue
        # user = 销售顾问，assistant = 客户
        speaker = "销售顾问" if role == "user" else "客户"
        dialogue_lines.append(f"{speaker}：{content}")

    dialogue_text = "\n\n".join(dialogue_lines)
    if not dialogue_text:
        raise HTTPException(status_code=400, detail="对话历史为空，无法评价")

    # 切换到教练 Agent
    manager.switch_agent("coach")

    # 构建评价请求
    evaluation_prompt = build_evaluation_prompt(
        scenario, req.student_name, req.student_id, dialogue_text
    )

    try:
        # 必须走 AgentLoop，不能裸调 LLM：
        # 教练的 95 行评价体系（5 个维度、输出格式、评分标准）在
        # workspace_coach/IDENTITY.md 里，只有 ContextManager 会把它拼进
        # system prompt。裸调用等于对一个看不见评价体系的模型说"按你的评价体系评"，
        # 结果就是它自己编一套 4 维度/100 分制——而且不报错。
        eval_session = session_mgr.new_session(work_dir="agent:coach")
        loop = manager.get_or_create_loop(eval_session)
        evaluation = loop.run(evaluation_prompt)
        # 总分是派生值，不该由模型算：实测出现过「总分 24 / 五维之和 20」。
        # 解析不完整时此处原样放行（质检器 T-TOTAL-01 会继续抓）。
        evaluation, total_info = reconcile_report_total(
            evaluation, COACH_EVALUATION_DIMENSIONS
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"生成评价失败：{exc}")
    finally:
        # 评价完成后恢复客户 Agent 上下文（用户可能返回对话页继续练习）
        manager.switch_agent("customer")

    return {
        "evaluation": evaluation,
        "student_name": req.student_name,
        "student_id": req.student_id,
        "scenario": scenario.title,
        # 便于排查"报告总分看着不对"：ok/fixed/no_total/partial/out_of_range
        "total_reconciled": total_info.get("reason"),
    }
