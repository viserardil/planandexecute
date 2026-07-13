
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value) -> str:
    """date-time alanı asla None olmasın (şema string zorunlu kılıyor)."""
    return value or _now()


def _clip(value, limit):
    """Uzun metni limit karakterde kırpar (0/None → sınırsız). Kırpınca ham uzunluğu
    işaretler ki bilgi kaybı görünür olsun."""
    if not limit or value is None:
        return value
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[kesildi, ham {len(text)} kar]"


def _tool_call(ev: dict, obs_limit=None) -> dict:
    """Bir araç olayını şemadaki ToolCall'a çevirir."""
    output = _clip(ev.get("output"), obs_limit)
    return {
        "tool_call_id": f"tc-{uuid.uuid4()}",
        "tool_name": ev["name"],
        "tool_input": {"input": ev.get("input", "")},
        "tool_output": output,
        "success": bool(ev.get("success")),
        "error_message": None if ev.get("success") else output,
        "latency_ms": round(ev.get("duration_ms", 0.0), 3),
    }


def _llm_call(ev: dict) -> dict:
    """Bir LLM olayını şemadaki LLMCall'a çevirir."""
    return {
        "llm_call_id": f"llm-{uuid.uuid4()}",
        "input_tokens": ev.get("input_tokens", 0),
        "output_tokens": ev.get("output_tokens", 0),
        "duration_ms": round(ev.get("duration_ms", 0.0), 3),
    }


def _group_steps(events: list[dict]) -> list[dict]:
    """Sıralı olayları adımlara gruplar: her LLM yeni adım açar, ardından gelen
    araçlar o adıma iliştirilir."""
    groups: list[dict] = []
    current: dict | None = None
    for ev in events:
        if ev["kind"] == "llm":
            current = {"llm": ev, "tools": []}
            groups.append(current)
        else:  # tool
            if current is None:  # olası: LLM'siz araç (normalde olmaz)
                current = {"llm": None, "tools": []}
                groups.append(current)
            current["tools"].append(ev)
    return groups


def _steps(events: list[dict], success: bool, obs_limit=None) -> list[dict]:
    groups = _group_steps(events)
    steps: list[dict] = []
    for i, g in enumerate(groups):
        is_last = i == len(groups) - 1
        llm = g["llm"]
        tools = g["tools"]

        if tools:
            step_type = "tool_call"
        elif is_last and success:
            step_type = "synthesis"   # nihai cevabı üreten adım (replanner/Response)
        else:
            step_type = "planning"    # planner ya da ara replan (araçsız akıl yürütme)

        ordered = ([llm] if llm else []) + tools
        started = ordered[0]["started_at"] if ordered else _now()
        ended = ordered[-1]["ended_at"] if ordered else _now()
        duration_ms = sum(e.get("duration_ms", 0.0) for e in ordered)

        steps.append({
            "step_id": f"step-{uuid.uuid4()}",
            "step_index": i,                       # 0-indexed
            "step_type": step_type,
            "started_at": _iso(started),
            "ended_at": _iso(ended),
            "duration_ms": round(duration_ms, 3),
            "reasoning_content": None,
            "input_context": None,
            "output": _clip((llm or {}).get("text"), obs_limit),  # modelin bu adımdaki metni
            "tool_calls": [_tool_call(t, obs_limit) for t in tools],
            "llm_calls": [_llm_call(llm)] if llm else [],
        })
    return steps


def to_run_result_schema(
    run_result,
    *,
    prompt,
    model,
    model_params,
    case_id,
    framework="plan-execute",
    repetition_index=1,
    is_synthetic=False,
    case_metadata=None,
    run_id=None,
    error_info=None,
    obs_limit=None,
):
    """RunResult'ı v2.0.0 RunResult şemasına uyan bir dict'e çevirir.

    prompt        : agent'a verilen tam soru
    model         : koşumun modeli (ör. HF_MODEL)
    model_params  : {"temperature": float, "max_tokens": int, ...}
    case_id       : görev/senaryo kimliği (gruplama anahtarı)
    framework     : framework adı ("plan-execute")
    error_info    : status=error ise {"error_type","error_message","failed_step_id"}
    obs_limit     : >0 ise uzun tool çıktıları / adım metinleri bu karakterde kırpılır
                    (final_answer ASLA kırpılmaz — scorer ona bakar). None/0 = sınırsız.
    """
    events = run_result.events
    steps = _steps(events, run_result.success, obs_limit)

    agent = {
        "agent_id": "plan-execute-0",
        "role": "plan-execute",
        "system_prompt_hash": None,
        "steps": steps,
        "tokens": {
            "input": run_result.input_tokens,
            "output": run_result.output_tokens,
        },
    }

    status = "error" if error_info else run_result.status
    tool_ms = sum(e.get("duration_ms", 0.0) for e in events if e["kind"] == "tool")
    llm_ms = sum(e.get("duration_ms", 0.0) for e in events if e["kind"] == "llm")

    return {
        "schema_version": "2.0.0",
        "run_id": run_id or str(uuid.uuid4()),
        "framework": framework,
        "repetition_index": repetition_index,
        "case_id": str(case_id),
        "case_metadata": case_metadata,
        "is_synthetic": is_synthetic,
        "prompt": prompt,
        "model": model,
        "model_params": model_params,
        "num_agents": 1,
        "total_steps": len(steps),
        "agents": [agent],
        "final_answer": run_result.answer,  # ASLA kırpılmaz — scorer buna bakar
        "status": status,
        "error_info": error_info,
        "tokens": {
            "input": run_result.input_tokens,
            "output": run_result.output_tokens,
            "total": run_result.total_tokens,
        },
        "latency": {
            "total_ms": round(run_result.elapsed_seconds * 1000, 3),
            "planning_ms": None,
            "tool_execution_ms": round(tool_ms, 3),
            "llm_inference_ms": round(llm_ms, 3),
        },
        "timestamps": {
            "started_at": _iso(run_result.started_at),
            "ended_at": _iso(run_result.ended_at),
        },
        "trace_id": None,
        # Ortak şemaya sığmayan plan-execute'a özgü olgular.
        "framework_specific": {
            "plan_steps": run_result.plan_steps,
            "replan_count": run_result.replan_count,
            "plan_trace": [
                {"step": t.step, "result": _clip(t.result, obs_limit)}
                for t in run_result.trace
            ],
        },
    }
