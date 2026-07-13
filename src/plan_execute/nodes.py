

from __future__ import annotations

from functools import lru_cache

from langgraph.prebuilt import create_react_agent

from plan_execute.config import get_llm, structured_output_kwargs
from plan_execute.prompts import PLANNER_PROMPT, REPLANNER_PROMPT
from plan_execute.schemas import Act, PlannerDecision, Response
from plan_execute.state import PlanExecute
from plan_execute.tools import get_tools

# LLM/ajan nesneleri TEMBEL (lazy) kurulur: modül import edilirken değil, ilk
# kullanımda. Böylece paketi HF_TOKEN olmadan import etmek çökmez (token yalnızca
# gerçekten çalıştırılırken gerekir) ve main.py dostça hata mesajını basabilir.


@lru_cache(maxsize=1)
def _get_executor_agent():
    """Adımları yürüten ReAct alt-ajanı (create_react_agent)."""
    return create_react_agent(get_llm(), get_tools())


# Structured output VARSAYILAN (json_schema) yöntemle yapılır. Neden:
#   - Sağlayıcı :deepinfra'ya sabitlendiği için json_schema destekleniyor (novita
#     desteklemiyordu → 400; bkz config.DEFAULT_MODEL).
#   - Replanner'ın Act şeması Union[Response, Plan] içerir; json_schema bu union'ı
#     üretim anında ZORLAR ve güvenilir parse eder. function_calling ise union'da
#     bazen 'action'a düz string koyup pydantic ValidationError'a yol açar (0/3).
@lru_cache(maxsize=1)
def _get_planner():
    """Planner zinciri. PlannerDecision döndürür: araç gerektiren görevlerde plan
    adımları (steps), gerektirmeyenlerde (kapsam dışı/selamlama/netleştirme)
    doğrudan cevap (direct_answer) → basit sorularda plan-execute döngüsü çalışmaz."""
    return PLANNER_PROMPT | get_llm().with_structured_output(PlannerDecision, **structured_output_kwargs())


@lru_cache(maxsize=1)
def _get_replanner():
    """Devam mı bitiş mi kararını veren replanner zinciri."""
    return REPLANNER_PROMPT | get_llm().with_structured_output(Act, **structured_output_kwargs())


def plan_step(state: PlanExecute) -> dict:
    """İlk kararı verir: araç gerekiyorsa PLAN üretir; gerekmiyorsa doğrudan
    CEVABI döndürür. İkinci durumda koşullu kenar (should_end) cevabı görüp
    END'e gider — executor/replanner hiç çalışmaz, plan-execute döngüsü dönmez."""
    decision = _get_planner().invoke({"messages": [("user", state["input"])]})
    # Araç gerekmez + doğrudan cevap varsa: hemen bitir.
    if not decision.needs_tools and decision.direct_answer.strip():
        return {"response": decision.direct_answer}
    # Araç gerekli: plan üret. Emniyet: adım boş dönerse görevi tek adım yap.
    return {"plan": decision.steps or [state["input"]]}


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
    """Sonuçlara göre planı günceller veya nihai cevabı üretir.

    Nihai cevap (Response) üretmek bir "bitir" kararıdır, plan revizyonu değildir;
    o yüzden replan_count YALNIZCA yeni bir Plan döndürüldüğünde artırılır.
    """
    output = _get_replanner().invoke(state)
    if isinstance(output.action, Response):
        return {"response": output.action.response}
    return {"plan": output.action.steps, "replan_count": 1}


def should_end(state: PlanExecute) -> str:
    """Koşullu kenar: cevap üretildiyse bitir, değilse yürütmeye devam et."""
    if state.get("response"):
        return "__end__"
    return "executor"
