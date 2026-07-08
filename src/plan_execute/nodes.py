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

from functools import lru_cache

from langgraph.prebuilt import create_react_agent

from plan_execute.config import get_llm
from plan_execute.prompts import PLANNER_PROMPT, REPLANNER_PROMPT
from plan_execute.schemas import Act, Plan, Response
from plan_execute.state import PlanExecute
from plan_execute.tools import get_tools

# LLM/ajan nesneleri TEMBEL (lazy) kurulur: modül import edilirken değil, ilk
# kullanımda. Böylece paketi HF_TOKEN olmadan import etmek çökmez (token yalnızca
# gerçekten çalıştırılırken gerekir) ve main.py dostça hata mesajını basabilir.


@lru_cache(maxsize=1)
def _get_executor_agent():
    """Adımları yürüten ReAct alt-ajanı (create_react_agent)."""
    return create_react_agent(get_llm(), get_tools())


@lru_cache(maxsize=1)
def _get_planner():
    """Structured output ile plan üreten planner zinciri."""
    return PLANNER_PROMPT | get_llm().with_structured_output(Plan)


@lru_cache(maxsize=1)
def _get_replanner():
    """Devam mı bitiş mi kararını veren replanner zinciri."""
    return REPLANNER_PROMPT | get_llm().with_structured_output(Act)


def plan_step(state: PlanExecute) -> dict:
    """Görev için ilk planı üretir."""
    plan = _get_planner().invoke({"messages": [("user", state["input"])]})
    return {"plan": plan.steps}


def execute_step(state: PlanExecute) -> dict:
    """Plandaki ilk adımı ReAct alt-ajanıyla yürütür."""
    plan = state["plan"]
    plan_str = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan))
    task = plan[0]
    task_prompt = (
        f"Şu plan için:\n{plan_str}\n\n"
        f"1. adımı yürütmen isteniyor: {task}"
    )
    agent_response = _get_executor_agent().invoke(
        {"messages": [("user", task_prompt)]}
    )
    result = agent_response["messages"][-1].content
    return {"past_steps": [(task, result)]}


def replan_step(state: PlanExecute) -> dict:
    """Sonuçlara göre planı günceller veya nihai cevabı üretir."""
    output = _get_replanner().invoke(state)
    if isinstance(output.action, Response):
        return {"response": output.action.response}
    return {"plan": output.action.steps}


def should_end(state: PlanExecute) -> str:
    """Koşullu kenar: cevap üretildiyse bitir, değilse yürütmeye devam et."""
    if state.get("response"):
        return "__end__"
    return "executor"
