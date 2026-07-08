"""Plan-Execute grafiğini çalıştıran ve metrik toplayan sarmalayıcı ajan.

ReAct tarafındaki ``ReActAgent.run() -> RunResult`` ile AYNI arayüzü ve AYNI
metrik şemasını sağlar; böylece iki mimari doğrudan (harmanlama yapmadan) aynı
tablo üzerinde karşılaştırılabilir.

Metrikler bir LangChain callback handler ile TÜM grafiği kapsayacak şekilde
toplanır: planner + executor'ın iç ReAct turları + her replan çağrısı. Callback
alt-çalıştırmalara (executor'ın create_react_agent'ı) da yayıldığı için token,
LLM çağrısı ve araç çağrısı sayıları eksiksiz sayılır.

Metrik eşlemesi (ReAct ↔ Plan-Execute), her ikisi de dürüst/mimariye özgü:
- ``steps``      : ReAct'te reasoning turu = 1 LLM çağrısı. Plan-Execute'ta da
                   toplam LLM çağrısı (planner + executor turları + replan).
                   İki tarafta da tanım aynı: "bir LLM invoke". Ayrıca aşağıdaki
                   plan-execute'a özgü sayaçlar da kaydedilir.
- ``tool_calls`` : Yürütülen araç sayısı (executor içinde).
- ``*_tokens``   : Token tüketimi.
- ``plan_steps`` : (Plan-Execute'a özgü) Yürütülen plan adımı sayısı.
- ``replan_count``: (Plan-Execute'a özgü) Replanner'ın çağrılma sayısı.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from langchain_core.callbacks.base import BaseCallbackHandler
from langgraph.errors import GraphRecursionError

from plan_execute.graph import build_plan_execute_graph


# --- Metrik toplama ---------------------------------------------------------


class MetricsCallback(BaseCallbackHandler):
    """Grafik boyunca LLM çağrısı, token ve araç kullanımını biriktirir."""

    def __init__(self) -> None:
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0
        self.tools_used: list[str] = []

    def on_llm_end(self, response, **kwargs) -> None:  # noqa: ANN001
        self.llm_calls += 1
        # 1) Sohbet modelleri: generations[i][j].message.usage_metadata
        for generation_list in getattr(response, "generations", []) or []:
            for generation in generation_list:
                message = getattr(generation, "message", None)
                usage = getattr(message, "usage_metadata", None) if message else None
                if usage:
                    self.input_tokens += usage.get("input_tokens", 0) or 0
                    self.output_tokens += usage.get("output_tokens", 0) or 0
                    return
        # 2) Yedek: llm_output.token_usage (bazı sağlayıcılar burada döner)
        llm_output = getattr(response, "llm_output", None) or {}
        token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        self.input_tokens += token_usage.get("prompt_tokens", 0) or 0
        self.output_tokens += token_usage.get("completion_tokens", 0) or 0

    def on_tool_start(self, serialized, input_str, **kwargs) -> None:  # noqa: ANN001
        name = (serialized or {}).get("name") or "bilinmeyen_araç"
        self.tools_used.append(name)
        self.tool_calls += 1

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# --- Adım izi ve sonuç ------------------------------------------------------


@dataclass
class PlanStepTrace:
    """Yürütülen tek bir plan adımının izi (adım metni + executor sonucu)."""

    step: str
    result: str


@dataclass
class RunResult:
    """Bir görevin çalıştırılmasının sonucu ve performans metrikleri.

    Alan adları ReAct ``RunResult`` ile bilerek ortaktır; ``plan_steps`` ve
    ``replan_count`` plan-execute'a özgü ek sayaçlardır.
    """

    question: str
    answer: str | None
    success: bool
    steps: int  # = llm_calls (ReAct'in reasoning turlarıyla aynı tanım)
    tool_calls: int
    input_tokens: int
    output_tokens: int
    elapsed_seconds: float
    llm_calls: int
    plan_steps: int
    replan_count: int
    tools_used: list[str] = field(default_factory=list)
    trace: list[PlanStepTrace] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# --- Ajan -------------------------------------------------------------------


class PlanExecuteAgent:
    """Plan-Execute grafiğini bir görev üzerinde çalıştırıp metrik döndürür."""

    def __init__(
        self,
        model: str | None = None,
        recursion_limit: int = 50,
        verbose: bool = True,
    ) -> None:
        # model parametresi ReAct arayüzüyle simetri için kabul edilir; LLM
        # fabrikası (config.get_llm) env üzerinden modelini seçer. Farklı bir
        # model gerekiyorsa HF_MODEL env değişkeni kullanılır.
        self.recursion_limit = recursion_limit
        self.verbose = verbose
        self.graph = build_plan_execute_graph()

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def run(self, question: str) -> RunResult:
        metrics = MetricsCallback()
        config = {"callbacks": [metrics], "recursion_limit": self.recursion_limit}

        started = time.perf_counter()
        answer: str | None = None
        past_steps: list = []
        try:
            state = self.graph.invoke({"input": question}, config=config)
            answer = state.get("response") or None
            past_steps = state.get("past_steps") or []
        except GraphRecursionError:
            # Özyineleme sınırına ulaşıldı: cevaba ulaşılamadı (ReAct'in max_steps
            # ile "cevap yok" davranışının plan-execute karşılığı).
            self._log("[UYARI] Özyineleme sınırına ulaşıldı; nihai cevap üretilemedi.")
        elapsed = time.perf_counter() - started

        trace = [PlanStepTrace(step=s, result=r) for s, r in past_steps]

        return RunResult(
            question=question,
            answer=answer,
            success=answer is not None,
            steps=metrics.llm_calls,
            tool_calls=metrics.tool_calls,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            elapsed_seconds=elapsed,
            llm_calls=metrics.llm_calls,
            plan_steps=len(past_steps),
            replan_count=len(past_steps),
            tools_used=metrics.tools_used,
            trace=trace,
        )
