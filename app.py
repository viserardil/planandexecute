"""FastAPI sunucusu — Plan-Execute ajanını basit bir sohbet UI'ıyla test etmek için.

Kök dizindeki index.html (React arayüzü) `/` adresinden sunulur; ajan çağrıları
göreli URL'lerle (/api/chat, /api/reset) aynı origin'e gider. Ajan TEKİL tutulur:
`PlanExecuteAgent._memory` thread_id bazlı olduğu için aynı sohbet (thread_id)
içinde önceki turlar hatırlanır — 'Temizle' butonu /api/reset ile belleği siler.

Çalıştırma:
    $env:UV_LINK_MODE = "copy"
    uv run uvicorn app:app --port 8000
    # sonra tarayıcıda: http://localhost:8000
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

# src/ layout: paket kurulu olmasa da import edilebilsin.
ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from plan_execute.agent import PlanExecuteAgent, build_detail_trace  # noqa: E402

app = FastAPI(title="Plan-Execute Ajan — Sohbet")

# Tek ajan örneği: graf kurulumu tembel LLM kullandığı için HF_TOKEN olmadan da
# oluşturulur (token yalnızca çalıştırma anında gerekir). _memory turlar arası
# belleği thread_id'ye göre saklar.
agent = PlanExecuteAgent(
    recursion_limit=int(os.getenv("RECURSION_LIMIT", "50")),
    verbose=False,
)


class ChatIn(BaseModel):
    message: str
    thread_id: str | None = None


class ResetIn(BaseModel):
    thread_id: str | None = None


@app.get("/")
def index() -> FileResponse:
    """React sohbet arayüzünü sunar."""
    return FileResponse(ROOT / "index.html")


@app.post("/api/chat")
def chat(body: ChatIn) -> dict:
    """Bir mesajı ajana verir, cevabı + metrikleri + ayrıntılı trace'i döner.

    Ağ/model hatası koşuyu düşürmez; hata mesajı 'answer' içinde döner ki UI
    kırılmadan gösterebilsin.
    """
    try:
        rr = agent.run(body.message, thread_id=body.thread_id)
        # Ayrıntılı, sıralı trace (test live-log'uyla AYNI format): plan başlığı +
        # her araç çağrısı (araç/girdi/çıktı/süre). UI'ın Step şekliyle uyumlu.
        trace = build_detail_trace(rr, obs_limit=2000)
        return {
            "answer": rr.answer,
            "success": rr.success,
            "steps": rr.steps,               # = llm_calls (planner+executor+replan)
            "tool_calls": rr.tool_calls,
            "tools_used": rr.tools_used,
            "total_tokens": rr.total_tokens,
            "duration_ms": int(rr.elapsed_seconds * 1000),
            "trace": trace,
        }
    except Exception as exc:  # noqa: BLE001 - UI'a temiz hata döndür
        return {
            "answer": f"Hata: {type(exc).__name__}: {exc}",
            "success": False,
            "steps": 0,
            "tool_calls": 0,
            "tools_used": [],
            "total_tokens": 0,
            "duration_ms": 0,
            "trace": [],
        }


@app.post("/api/reset")
def reset(body: ResetIn) -> dict:
    """Bir thread'in kısa süreli belleğini siler ('Temizle' butonu)."""
    removed = agent.reset_memory(body.thread_id) if body.thread_id else False
    return {"ok": True, "removed": removed}
