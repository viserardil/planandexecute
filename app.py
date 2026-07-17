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

import logging
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

# --- Terminal log'u ---------------------------------------------------------
# Her istekte: soru → her LLM turu / araç çağrısı (canlı) → özet; hatada TAM
# traceback. Sorun çıktığında terminalden ne olduğunu görebilmek için.
# Sessizleştirmek istersen: LOG_LEVEL=WARNING
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("plan-execute")


def _one(value, limit=160):
    """Tek satıra indirip kırpar (log okunaklı kalsın)."""
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"

# src/ layout: paket kurulu olmasa da import edilebilsin.
ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from plan_execute.agent import PlanExecuteAgent, build_detail_trace  # noqa: E402
from plan_execute.config import settings  # noqa: E402
from plan_execute.tools import CHARTS_DIR  # noqa: E402

app = FastAPI(title="Plan-Execute Ajan — Sohbet")

# Grafik araçları (plot_chart / visualize_data) PNG'yi diske yazıp yalnızca YOLUNU
# döner. Tarayıcı diskten okuyamaz; bu yüzden charts dizinini statik olarak sunuyoruz
# ve /api/chat cevabında dosya yolları -> /charts/<dosya> URL'lerine çevriliyor.
os.makedirs(CHARTS_DIR, exist_ok=True)
app.mount("/charts", StaticFiles(directory=CHARTS_DIR), name="charts")

_PNG_RE = re.compile(r"[\w.\-]+\.png", re.IGNORECASE)


def _collect_charts(rr) -> list[str]:
    """Koşuda üretilen grafiklerin URL'lerini (sırayla, tekrarsız) toplar.

    Araç çıktılarını ve nihai cevabı .png adı için tarar; SADECE basename kullanılır
    ve CHARTS_DIR içinde gerçekten var mı diye bakılır — yani ne yol taşması olur ne
    de modelin uydurduğu bir dosya adı URL'ye dönüşür.
    """
    urls: list[str] = []
    texts = [str(ev.get("output") or "") for ev in rr.events if ev.get("kind") == "tool"]
    texts.append(str(rr.answer or ""))
    for text in texts:
        for name in _PNG_RE.findall(text):
            url = f"/charts/{name}"
            if url not in urls and os.path.isfile(os.path.join(CHARTS_DIR, name)):
                urls.append(url)
    return urls

# Başlangıçta aktif yapılandırmayı bas — "hangi model/endpoint'e gidiyorum?"
# sorusu hata ayıklamanın yarısı. Anahtar ASLA loglanmaz, sadece var/yok.
log.info("Yapılandırma: model=%s · endpoint=%s · anahtar=%s · timeout=%ss · retry=%s",
         settings.model_name or "(TANIMSIZ!)", settings.base_url or "(sağlayıcı varsayılanı)",
         "var" if settings.api_key else "YOK!", settings.timeout, settings.max_retries)

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
    kırılmadan gösterebilsin. Her adım ayrıca terminale loglanır (teşhis için).
    """
    t0 = time.time()
    log.info("▶ [%s] SORU: %s", body.thread_id or "-", _one(body.message, 140))
    try:
        # Canlı adım log'u: her LLM turu / araç çağrısı bitince terminale düşer.
        def _log_event(ev):
            if ev.get("kind") == "tool":
                mark = "✅" if ev.get("success", True) else "❌"
                log.info("   · %s(%s) %s %.0fms", ev.get("name"),
                         _one(ev.get("input", ""), 60), mark, ev.get("duration_ms", 0))
                if ev.get("output"):
                    log.info("        → %s", _one(ev["output"], 180))
            else:
                log.info("   · … LLM turu (%.1fsn, +%d tok)",
                         ev.get("duration_ms", 0) / 1000, ev.get("output_tokens", 0))

        rr = agent.run(body.message, thread_id=body.thread_id, on_event=_log_event)
        # Ayrıntılı, sıralı trace (test live-log'uyla AYNI format): plan başlığı +
        # her araç çağrısı (araç/girdi/çıktı/süre). UI'ın Step şekliyle uyumlu.
        trace = build_detail_trace(rr, obs_limit=2000)
        images = _collect_charts(rr)
        if images:
            log.info("   🖼 grafik: %s", ", ".join(images))
        log.info("%s %s | adım=%d plan=%d replan=%d araç=%d (%s) token=%d süre=%.1fsn",
                 "✅" if rr.success else "⚠️", rr.status, rr.steps, rr.plan_steps,
                 rr.replan_count, rr.tool_calls, ", ".join(rr.tools_used) or "-",
                 rr.total_tokens, rr.elapsed_seconds)
        log.info("   CEVAP: %s", _one(rr.answer, 200))
        return {
            "answer": rr.answer,
            "success": rr.success,
            "steps": rr.steps,               # = llm_calls (planner+executor+replan)
            "tool_calls": rr.tool_calls,
            "tools_used": rr.tools_used,
            "total_tokens": rr.total_tokens,
            "duration_ms": int(rr.elapsed_seconds * 1000),
            "trace": trace,
            "images": images,        # /charts/... — UI bunları doğrudan gösterir
        }
    except Exception as exc:  # noqa: BLE001 - UI'a temiz hata döndür
        # Terminale TAM traceback (teşhis için); UI'a yalnızca kısa mesaj.
        log.exception("❌ HATA (%.1fsn sonra) — %s: %s",
                      time.time() - t0, type(exc).__name__, exc)
        return {
            "answer": f"Hata: {type(exc).__name__}: {exc}",
            "success": False,
            "steps": 0,
            "tool_calls": 0,
            "tools_used": [],
            "total_tokens": 0,
            "duration_ms": 0,
            "trace": [],
            "images": [],
        }


@app.post("/api/reset")
def reset(body: ResetIn) -> dict:
    """Bir thread'in kısa süreli belleğini siler ('Temizle' butonu)."""
    removed = agent.reset_memory(body.thread_id) if body.thread_id else False
    log.info("🗑 bellek sıfırlandı [%s] → %s", body.thread_id or "-", removed)
    return {"ok": True, "removed": removed}


if __name__ == "__main__":
    # Doğrudan çalıştırma: `uv run python app.py` → http://127.0.0.1:8000
    # Port/host env'den de ayarlanabilir (PORT / HOST).
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "8000")))
