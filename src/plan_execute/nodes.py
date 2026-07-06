"""Plan-Execute grafiğinin düğümleri: plan → execute → replan.

Mimari (Plan-and-Execute, Wang et al. 2023 / LangGraph):

    ┌─────────┐      ┌──────────┐      ┌───────────┐
    │ planner │ ───▶ │ executor │ ───▶ │ replanner │
    └─────────┘      └──────────┘      └─────┬─────┘
                          ▲                  │
                          └──────────────────┘  (plan bitmediyse)
                                             │
                                             ▼
                                          (bitti → END)

- planner:  Görev için baştan tüm planı çıkarır (bir kez).
- executor: Plandaki İLK adımı bir ReAct alt-ajanı ile yürütür.
- replanner: Kalan plana ve sonuçlara bakıp ya günceller ya da nihai cevabı verir.

Bu, ReAct'ten temel farktır: ReAct her adımda "düşün-eyle" döngüsü kurarken,
Plan-Execute önce planı çıkarır, sonra yürütür; bu genelde daha az LLM çağrısıyla
uzun görevlerde daha tutarlı olur — ölçeceğimiz şey tam olarak bu.
"""

from __future__ import annotations

from langgraph.prebuilt import create_react_agent

from plan_execute.config import get_llm
from plan_execute.prompts import PLANNER_PROMPT, REPLANNER_PROMPT
from plan_execute.schemas import Act, Plan, Response
from plan_execute.state import PlanExecute
from plan_execute.tools import get_tools

# --- Executor: adımları yürüten ReAct alt-ajanı ------------------------------
# Not: Burada tek bir adımı yürütmek için ReAct kullanıyoruz. ReAct'in kendisiyle
# karşılaştırma yaparken bunu ayrı bir grafik olarak da çalıştıracağız.
_executor_agent = create_react_agent(get_llm(), get_tools())

# --- Planner: structured output ile plan üretir ------------------------------
_planner = PLANNER_PROMPT | get_llm().with_structured_output(Plan)

# --- Replanner: devam mı bitiş mi kararını verir -----------------------------
_replanner = REPLANNER_PROMPT | get_llm().with_structured_output(Act)


async def plan_step(state: PlanExecute) -> dict:
    """Görev için ilk planı üretir."""
    plan = await _planner.ainvoke({"messages": [("user", state["input"])]})
    return {"plan": plan.steps}


async def execute_step(state: PlanExecute) -> dict:
    """Plandaki ilk adımı ReAct alt-ajanıyla yürütür."""
    plan = state["plan"]
    plan_str = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan))
    task = plan[0]
    task_prompt = (
        f"Şu plan için:\n{plan_str}\n\n"
        f"1. adımı yürütmen isteniyor: {task}"
    )
    agent_response = await _executor_agent.ainvoke(
        {"messages": [("user", task_prompt)]}
    )
    result = agent_response["messages"][-1].content
    return {"past_steps": [(task, result)]}


async def replan_step(state: PlanExecute) -> dict:
    """Sonuçlara göre planı günceller veya nihai cevabı üretir."""
    output = await _replanner.ainvoke(state)
    if isinstance(output.action, Response):
        return {"response": output.action.response}
    return {"plan": output.action.steps}


def should_end(state: PlanExecute) -> str:
    """Koşullu kenar: cevap üretildiyse bitir, değilse yürütmeye devam et."""
    if state.get("response"):
        return "__end__"
    return "executor"
