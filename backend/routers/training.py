"""
培训路由：模拟客户 + 教练评价。

GET  /api/training/scenarios           列出可用场景
POST /api/training/start               开始培训（学生信息 + 场景）
POST /api/training/evaluate            结束演练，教练评价
"""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from agent.session import Session, SessionManager

from ..core.agent_manager import AgentManager
from ..dependencies import get_agent_manager, get_session_manager
from ..training_scenarios import build_customer_system_prompt, get_scenario, list_scenarios

router = APIRouter(prefix="/api/training", tags=["training"])

# 用于移除 LLM 思考标签（<think>...</think>）的正则
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
# 用于移除孤立的未闭合 <think> 或 </think> 标签
_THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)


def _strip_think_tags(text: str) -> str:
    """移除 LLM 输出中的 <think> 思维链标签内容。"""
    if not text:
        return text
    text = _THINK_BLOCK_RE.sub("", text)
    text = _THINK_TAG_RE.sub("", text)
    return text.strip()


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
    session = session_mgr.new_session(
        work_dir="agent:customer",
    )

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
    evaluation_prompt = (
        f"请你作为 LV 销售培训教练，评价以下销售顾问的模拟演练表现。\n\n"
        f"【学员信息】\n"
        f"姓名：{req.student_name}\n"
        f"学号：{req.student_id}\n"
        f"模拟场景：{scenario.title} - {scenario.description}\n\n"
        f"【完整对话记录】\n{dialogue_text}\n\n"
        "请按照你的评价体系，给出结构化评估报告（包含各维度打分、亮点、待改进、示范话术、总结和下一步练习建议）。\n"
        "直接输出评价报告，不要包含任何思考过程。"
    )

    try:
        response = manager.llm.chat_blocking(
            messages=[
                {"role": "user", "content": evaluation_prompt},
            ],
        )
        evaluation = _strip_think_tags(response.content)
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
    }
