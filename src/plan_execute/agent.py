

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langchain_core.callbacks.base import BaseCallbackHandler
from langgraph.errors import GraphRecursionError

from plan_execute.graph import build_plan_execute_graph


# --- Yardımcılar ------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Araç çıktısındaki hata imzaları (react_scratch ile aynı sözleşme): araçlar
# başarısızlıkta tanınabilir Türkçe metinler döndürür.
_TOOL_ERROR_MARKERS = ("HATA:", "hatası:", "kurulu değil", "Ağ hatası", "ayarlı değil")


def _tool_ok(observation: str) -> bool:
    obs = (observation or "")[:80]
    return not any(m in obs for m in _TOOL_ERROR_MARKERS)


def _parse_llm_response(response) -> tuple[int, int, str | None]:
    """LLM yanıtından (girdi_token, çıktı_token, metin) çıkarır."""
    input_tokens = 0
    output_tokens = 0
    text: str | None = None
    for generation_list in getattr(response, "generations", []) or []:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            if text is None:
                content = getattr(message, "content", None) if message else getattr(generation, "text", None)
                text = content if isinstance(content, str) else None
            usage = getattr(message, "usage_metadata", None) if message else None
            if usage:
                input_tokens += usage.get("input_tokens", 0) or 0
                output_tokens += usage.get("output_tokens", 0) or 0
    if input_tokens == 0 and output_tokens == 0:
        llm_output = getattr(response, "llm_output", None) or {}
        token_usage = llm_output.get("token_usage") or llm_output.get("usage") or {}
        input_tokens += token_usage.get("prompt_tokens", 0) or 0
        output_tokens += token_usage.get("completion_tokens", 0) or 0
    return input_tokens, output_tokens, text


# --- Metrik toplama ---------------------------------------------------------


class MetricsCallback(BaseCallbackHandler):
    """Grafik boyunca LLM çağrısı, token, araç kullanımı ve sıralı olayları biriktirir.

    ``events``: her LLM/araç çağrısını süresi + zaman damgasıyla kronolojik tutar.
    run_schema bunları a.json adımlarına çevirir. Sayaçlar (llm_calls, tool_calls,
    tokens) main.py/eski test'in beklediği run-seviyesi özet için tutulur.
    """

    def __init__(self, on_event=None) -> None:
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.tool_calls = 0
        self.tools_used: list[str] = []
        self.events: list[dict] = []
        self._llm_t0: dict = {}
        self._tool_t0: dict = {}
        # Opsiyonel canlı kanca: her LLM/araç olayı bittiğinde çağrılır (vaka
        # içinde ilerleme göstermek için — ör. test live-log). Hata koşuyu düşürmez.
        self._on_event = on_event

    def _emit(self, event: dict) -> None:
        if self._on_event:
            try:
                self._on_event(event)
            except Exception:
                pass

    # LLM zamanlaması: sohbet modelleri on_chat_model_start + on_llm_end kullanır.
    def on_chat_model_start(self, serialized, messages, *, run_id=None, **kwargs) -> None:  # noqa: ANN001
        self._llm_t0[run_id] = (time.perf_counter(), _now_iso())

    def on_llm_start(self, serialized, prompts, *, run_id=None, **kwargs) -> None:  # noqa: ANN001
        self._llm_t0.setdefault(run_id, (time.perf_counter(), _now_iso()))

    def on_llm_end(self, response, *, run_id=None, **kwargs) -> None:  # noqa: ANN001
        t0, started = self._llm_t0.pop(run_id, (None, _now_iso()))
        duration_ms = (time.perf_counter() - t0) * 1000 if t0 is not None else 0.0
        input_tokens, output_tokens, text = _parse_llm_response(response)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.llm_calls += 1
        event = {
            "kind": "llm",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "text": text,
            "started_at": started,
            "ended_at": _now_iso(),
            "duration_ms": round(duration_ms, 3),
        }
        self.events.append(event)
        self._emit(event)

    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kwargs) -> None:  # noqa: ANN001
        name = (serialized or {}).get("name") or "bilinmeyen_araç"
        # Temiz girdi: tek-argümanlı araçta inputs={'tool_input': 'MSFT'} -> 'MSFT'.
        clean = input_str
        if isinstance(inputs, dict):
            clean = next(iter(inputs.values())) if len(inputs) == 1 else inputs
        self._tool_t0[run_id] = (time.perf_counter(), _now_iso(), name, clean)

    def on_tool_end(self, output, *, run_id=None, **kwargs) -> None:  # noqa: ANN001
        t0, started, name, tool_input = self._tool_t0.pop(
            run_id, (None, _now_iso(), "bilinmeyen_araç", "")
        )
        duration_ms = (time.perf_counter() - t0) * 1000 if t0 is not None else 0.0
        # create_react_agent aracı bir ToolMessage döndürür; ham metni .content'te.
        out = str(getattr(output, "content", output))
        self.tool_calls += 1
        self.tools_used.append(name)
        event = {
            "kind": "tool",
            "name": name,
            "input": str(tool_input),
            "output": out,
            "success": _tool_ok(out),
            "started_at": started,
            "ended_at": _now_iso(),
            "duration_ms": round(duration_ms, 3),
        }
        self.events.append(event)
        self._emit(event)

    def on_tool_error(self, error, *, run_id=None, **kwargs) -> None:  # noqa: ANN001
        t0, started, name, tool_input = self._tool_t0.pop(
            run_id, (None, _now_iso(), "bilinmeyen_araç", "")
        )
        duration_ms = (time.perf_counter() - t0) * 1000 if t0 is not None else 0.0
        self.tool_calls += 1
        self.tools_used.append(name)
        event = {
            "kind": "tool",
            "name": name,
            "input": str(tool_input),
            "output": str(error),
            "success": False,
            "started_at": started,
            "ended_at": _now_iso(),
            "duration_ms": round(duration_ms, 3),
        }
        self.events.append(event)
        self._emit(event)

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

    Alan adları ReAct ``RunResult`` ile bilerek ortaktır; ``plan_steps`` /
    ``replan_count`` plan-execute'a özgü ek sayaçlar, ``events`` ise a.json
    şemasına çeviri için sıralı LLM/araç olay listesidir. Tüm alanlar varsayılanlı
    olduğundan hata durumunda boş bir RunResult kolayca kurulabilir.
    """

    question: str = ""
    answer: str | None = None
    success: bool = False
    status: str = "success"  # success | partial | timeout | error
    steps: int = 0  # = llm_calls
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    elapsed_seconds: float = 0.0
    started_at: str | None = None
    ended_at: str | None = None
    llm_calls: int = 0
    plan_steps: int = 0
    replan_count: int = 0
    tools_used: list[str] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    trace: list[PlanStepTrace] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


# --- Ayrıntılı trace (UI ve test-log ORTAK formatı) -------------------------


def build_detail_trace(run_result: "RunResult", obs_limit: int | None = None) -> list[dict]:
    """RunResult'tan ayrıntılı, sıralı adım listesi üretir.

    Hem test live-log'u hem sohbet UI'ı bunu kullanır — böylece iki yerde AYNI
    format görünür. Yapı:
      - İlk adım (adim="Plan"): yürütülen plan adımlarını listeler.
      - Sonraki her adım: bir ARAÇ çağrısıdır — onu tetikleyen LLM turunun metni
        (thought, varsa), çağrılan araç (action/action_input), ham gözlem
        (observation) ve başarı/süre (ok/ms).

    Dönen sözlükler UI'ın Step şekliyle uyumludur (adim/thought/action/
    action_input/observation) + ek meta (ok/ms). obs_limit verilirse uzun
    metinler kırpılır.
    """
    def clip(value):
        if value is None:
            return None
        text = str(value)
        if obs_limit and len(text) > obs_limit:
            return text[: obs_limit - 1] + "…"
        return text

    steps: list[dict] = []

    # 0) Yürütülen planı özetleyen başlık adımı.
    if run_result.trace:
        plan_txt = "\n".join(
            f"{i}. {t.step}" for i, t in enumerate(run_result.trace, 1)
        )
        steps.append({
            "adim": "Plan", "thought": plan_txt,
            "action": None, "action_input": None, "observation": None,
            "ok": True, "ms": 0,
        })

    # 1) Her araç çağrısı, kendisini tetikleyen LLM turunun metniyle birlikte.
    pending_thought = None
    n = 0
    for ev in run_result.events:
        if ev["kind"] == "llm":
            pending_thought = (ev.get("text") or "").strip() or None
        else:  # tool
            n += 1
            steps.append({
                "adim": n,
                "thought": clip(pending_thought),
                "action": ev.get("name"),
                "action_input": clip(ev.get("input")),
                "observation": clip(ev.get("output")),
                "ok": bool(ev.get("success", True)),
                "ms": round(ev.get("duration_ms", 0.0), 1),
            })
            pending_thought = None

    return steps


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
        # fabrikası (config.get_llm) modelini env üzerinden (HF_MODEL) seçer.
        self.recursion_limit = recursion_limit
        self.verbose = verbose
        self.graph = build_plan_execute_graph()
        # Kısa süreli (thread bazlı) konuşma belleği — react_scratch ile aynı API.
        # thread_id -> [(soru, cevap), ...]. Eval'de kullanılmaz (thread_id=None).
        self._memory: dict[str, list[tuple[str, str]]] = {}

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def reset_memory(self, thread_id: str) -> bool:
        """Bir thread'in kısa süreli belleğini siler; silinecek şey varsa True."""
        return self._memory.pop(thread_id, None) is not None

    @staticmethod
    def _with_memory(question: str, history: list[tuple[str, str]]) -> str:
        """Önceki (soru, cevap) çiftlerini kısa bir bağlam önsözü olarak göreve
        ekler. Bellek yoksa görev metni değişmeden döner (eval çıktısı aynı kalır).
        Yalnızca SON 5 tur tutulur — 'kısa' süreli bellek."""
        if not history:
            return question
        lines = ["Önceki konuşma (bağlam):"]
        for q, a in history[-5:]:
            lines.append(f"S: {q}")
            lines.append(f"C: {a}")
        lines.append("")
        lines.append(f"Yeni görev: {question}")
        return "\n".join(lines)

    def run(self, question: str, thread_id: str | None = None, on_event=None) -> RunResult:
        # on_event(event): her LLM/araç olayı bittiğinde çağrılır — vaka içinde
        # canlı ilerleme göstermek için (test live-log, UI stream vb.).
        metrics = MetricsCallback(on_event=on_event)
        config = {"callbacks": [metrics], "recursion_limit": self.recursion_limit}

        history = self._memory.get(thread_id, []) if thread_id else []
        graph_input = self._with_memory(question, history)

        started = time.perf_counter()
        started_iso = _now_iso()
        answer: str | None = None
        past_steps: list = []
        replan_count = 0
        status = "success"
        try:
            state = self.graph.invoke({"input": graph_input}, config=config)
            answer = state.get("response") or None
            past_steps = state.get("past_steps") or []
            replan_count = state.get("replan_count", 0) or 0
            status = "success" if answer else "partial"
        except GraphRecursionError:
            # Özyineleme sınırına ulaşıldı: cevaba ulaşılamadı (ReAct'in max_steps
            # ile "cevap yok / partial" davranışının plan-execute karşılığı).
            self._log("[UYARI] Özyineleme sınırına ulaşıldı; nihai cevap üretilemedi.")
            status = "partial"
        elapsed = time.perf_counter() - started
        ended_iso = _now_iso()

        trace = [PlanStepTrace(step=s, result=r) for s, r in past_steps]

        # Turu kısa süreli belleğe yaz (yalnızca başarılı cevapları hatırla).
        if thread_id and answer:
            self._memory.setdefault(thread_id, []).append((question, answer))

        return RunResult(
            question=question,
            answer=answer,
            success=answer is not None,
            status=status,
            steps=metrics.llm_calls,
            tool_calls=metrics.tool_calls,
            input_tokens=metrics.input_tokens,
            output_tokens=metrics.output_tokens,
            elapsed_seconds=elapsed,
            started_at=started_iso,
            ended_at=ended_iso,
            llm_calls=metrics.llm_calls,
            plan_steps=len(past_steps),
            replan_count=replan_count,
            tools_used=metrics.tools_used,
            events=metrics.events,
            trace=trace,
        )
