

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
#   - Seçilen model/sağlayıcı json_schema desteklemeli. (HF Router + Qwen kullanıyorsan
#     model adını :deepinfra'ya sabitle; novita desteklemiyor → 400. Desteklemeyen bir
#     sağlayıcıda LLM_STRUCTURED_METHOD=function_calling ile geçebilirsin.)
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


def _clip(value, limit: int) -> str:
    """Uzun metni kırpar; kırpınca ham uzunluğu işaretler (kayıp görünür olsun)."""
    text = str(value if value is not None else "")
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[kesildi, ham {len(text)} kar]"


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


# Önceki adım sonuçları prompt'a eklenirken bu uzunlukta kırpılır. Amaç bağlamı
# TAŞIMAK, veriyi tekrar etmek değil — araç gerekirse yeniden çağrılabilir.
_PAST_RESULT_LIMIT = 800


def execute_step(state: PlanExecute) -> dict:
    """Plandaki ilk adımı ReAct alt-ajanıyla yürütür.

    Alt-ajan her çağrıda SIFIRDAN kurulur; turlar arası hafızası yoktur. Bu yüzden
    ihtiyaç duyacağı bağlam prompt'a AÇIKÇA konur: kullanıcının asıl isteği + o ana
    kadar tamamlanmış adımların sonuçları. Aksi halde "elde edilen veriyle grafiği
    çiz" gibi bir adımda hangi hisseden söz edildiğini bilemez ve kullanıcıya
    "hangi sembol?" diye sorar — 55 vakalık koşuda 8 vakada (hepsi 2. adım ve
    sonrasında) olan buydu; replanner sembolü adıma geri yazana kadar bir tur boşa
    gidiyordu.
    """
    plan = state["plan"]
    if not plan:  # emniyet: boş plan gelirse patlama, replanner karar versin
        return {}
    plan_str = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(plan))
    task = plan[0]

    parts = [
        f"Kullanıcının asıl isteği: {state['input']}",
        "",
        f"Plan:\n{plan_str}",
    ]
    past = state.get("past_steps") or []
    if past:
        done = "\n".join(
            f"{i}. {step}\n   → {_clip(result, _PAST_RESULT_LIMIT)}"
            for i, (step, result) in enumerate(past, 1)
        )
        parts += ["", f"Şimdiye kadar tamamlanan adımlar ve sonuçları:\n{done}"]
    parts += [
        "",
        f"Şimdi şu adımı yürüt: {task}",
        "Gereken bilgi (sembol, dönem, şirket vb.) yukarıdaki bağlamda varsa "
        "kullanıcıya SORMA — doğrudan bağlamdan al ve adımı yürüt.",
        # Adım disiplini: bağlam verilince alt-ajan planın tamamını tek turda
        # bitirmeye eğilimli (gözlendi: 2 adımlık plan tek turda bitti,
        # plan_steps 2→1). Bu, Plan-Execute'u davranışta ReAct'e yaklaştırır ve
        # kıyasın "tek değişken mimari" ilkesini bozar. O yüzden açıkça sınırla.
        "YALNIZCA bu adımı yürüt — plandaki sonraki adımlara GEÇME, onları "
        "yapmaya çalışma. Bu adım bitince dur; sırayı replanner belirleyecek.",
    ]

    agent_response = _get_executor_agent().invoke(
        {"messages": [("user", "\n".join(parts))]}
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
    steps = output.action.steps
    if not steps:
        # Boş plan = yapacak yeni adım yok. Sonsuz döngü / execute_step'te plan[0]
        # IndexError yerine son adımın sonucunu nihai cevap yap.
        past = state.get("past_steps") or []
        return {"response": past[-1][1] if past else "Görev tamamlandı."}
    return {"plan": steps, "replan_count": 1}


def should_end(state: PlanExecute) -> str:
    """Koşullu kenar: cevap üretildiyse bitir, değilse yürütmeye devam et."""
    if state.get("response"):
        return "__end__"
    return "executor"
