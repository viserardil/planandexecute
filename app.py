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
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

load_dotenv()

# --- Log'lama ---------------------------------------------------------------
# İKİ KATMAN:
#   1) Terminal — yalnızca kısa özet (başladı / bitti + hata traceback'i).
#   2) Oturum dosyası — logs/<thread_id>.log: adım adım TAM akış (plan, her
#      düğümün kararı, model düşüncesi, araç çağrıları). Her sohbet kendi
#      dosyasına yazar; paralel sohbetler birbirine karışmaz ve koşu bittikten
#      sonra da incelenebilir.
# Ayarlar: LOG_LEVEL (varsayılan INFO) · LOG_DIR (varsayılan <proje>/logs)
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


# thread_id İSTEK GÖVDESİNDEN gelir (dışarıdan kontrol edilir); dosya adına
# girmeden önce ayraç/nokta içermeyen bir alt kümeye indirilir — aksi halde
# "../x" gibi bir değer log dizininin dışına yazabilirdi.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_-]")
_session_loggers: dict[str, logging.Logger] = {}
_session_lock = threading.Lock()


def _session_logger(thread_id) -> logging.Logger:
    """thread_id'ye özgü dosya logger'ı — ilk çağrıda kurulur, sonra önbellekten.

    propagate=False: oturum ayrıntısı terminale TEKRAR basılmaz (terminalde
    yalnızca özet kalsın diye).
    """
    safe = _UNSAFE_NAME.sub("_", str(thread_id or ""))[:64] or "anon"
    with _session_lock:
        logger = _session_loggers.get(safe)
        if logger is not None:
            return logger
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger(f"plan-execute.session.{safe}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        handler = logging.FileHandler(LOG_DIR / f"{safe}.log", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
        logger.addHandler(handler)
        logger.info("=" * 72)
        logger.info("OTURUM %s · başladı %s", safe, time.strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("model=%s · endpoint=%s",
                    settings.model_name or "(TANIMSIZ!)",
                    settings.base_url or "(sağlayıcı varsayılanı)")
        logger.info("(bu dosya canlı güncellenir — `tail -f` ile izleyebilirsin)")
        logger.info("=" * 72)
        _session_loggers[safe] = logger
        return logger


# Graf düğümlerinin log etiketleri.
_NODE_LABEL = {
    "planner": "🧠 PLANNER",
    "executor": "⚙️  EXECUTOR",
    "replanner": "🔁 REPLANNER",
}


def _log_node(slog: logging.Logger, ev: dict) -> None:
    """Bir düğümün başlangıcını/kararını yazar.

    Akışın iskeleti bu: planner'ın ürettiği plan, executor'ın o an yürüttüğü
    adım ve sonucu, replanner'ın 'devam mı bitir mi' kararı.
    """
    name = ev.get("name")
    label = _NODE_LABEL.get(name, str(name))

    if ev.get("phase") == "start":
        if name == "executor":
            kalan = len(ev.get("plan") or [])
            slog.info("%s ▶ adım yürütülüyor (planda %d adım kaldı):", label, kalan)
            slog.info("      → %s", _one(ev.get("step"), 300))
        else:
            slog.info("%s ▶ düşünüyor…", label)
        return

    # phase == "end" → düğümün kararı
    saniye = ev.get("duration_ms", 0) / 1000
    if ev.get("response"):
        if name == "planner":
            slog.info("%s ✓ araç GEREKMEZ → doğrudan cevap (%.1fsn)", label, saniye)
        else:
            slog.info("%s ✓ karar: BİTİR — nihai cevap hazır (%.1fsn)", label, saniye)
        slog.info("      → %s", _one(ev["response"], 400))
    elif ev.get("plan"):
        plan = ev["plan"]
        baslik = "PLAN üretildi" if name == "planner" else "karar: DEVAM — plan revize edildi"
        slog.info("%s ✓ %s — %d adım (%.1fsn):", label, baslik, len(plan), saniye)
        for i, step in enumerate(plan, 1):
            slog.info("      %d. %s", i, _one(step, 300))
    elif ev.get("result") is not None:
        slog.info("%s ✓ adım tamamlandı (%.1fsn)", label, saniye)
        slog.info("      sonuç: %s", _one(ev["result"], 600))

# src/ layout: paket kurulu olmasa da import edilebilsin.
ROOT = Path(__file__).resolve().parent
SRC_DIR = ROOT / "src"

# Oturum log'larının yazılacağı dizin (yukarıdaki _session_logger kullanır).
LOG_DIR = Path(os.getenv("LOG_DIR") or (ROOT / "logs"))
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
log.info("Oturum log'ları: %s  (her sohbet kendi <thread_id>.log dosyasına yazar)", LOG_DIR)

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
    slog = _session_logger(body.thread_id)
    log.info("▶ [%s] SORU: %s", body.thread_id or "-", _one(body.message, 140))
    slog.info("")
    slog.info("─" * 72)
    slog.info("▶ SORU: %s", _one(body.message, 500))
    slog.info("─" * 72)
    try:
        # Canlı adım log'u — oturum dosyasına, olay bitince anında.
        # Üç tür olay: düğüm sınırı (plan/karar), LLM turu (düşünce), araç çağrısı.
        def _log_event(ev):
            kind = ev.get("kind")
            if kind == "node":
                _log_node(slog, ev)
            elif kind == "tool":
                mark = "✅" if ev.get("success", True) else "❌"
                slog.info("      · %s(%s) %s %.0fms", ev.get("name"),
                          _one(ev.get("input", ""), 100), mark, ev.get("duration_ms", 0))
                if ev.get("output"):
                    slog.info("           → %s", _one(ev["output"], 400))
            else:  # llm turu
                slog.info("      · [%s] LLM turu (%.1fsn, +%d tok)",
                          ev.get("node") or "-", ev.get("duration_ms", 0) / 1000,
                          ev.get("output_tokens", 0))
                # Modelin bu turdaki ham çıktısı = scratchpad. planner/replanner'da
                # bu, structured output'un JSON'ı olur (kararın kendisi görünür);
                # executor'ın ReAct alt-ajanında ise düz akıl yürütme metnidir.
                thought = (ev.get("text") or "").strip()
                if thought:
                    slog.info("        💭 %s", _one(thought, 500))

        rr = agent.run(body.message, thread_id=body.thread_id, on_event=_log_event)
        # Ayrıntılı, sıralı trace (test live-log'uyla AYNI format): plan başlığı +
        # her araç çağrısı (araç/girdi/çıktı/süre). UI'ın Step şekliyle uyumlu.
        trace = build_detail_trace(rr, obs_limit=2000)
        images = _collect_charts(rr)
        if images:
            slog.info("   🖼 grafik: %s", ", ".join(images))
        ozet = ("%s %s | adım=%d plan=%d replan=%d araç=%d (%s) token=%d süre=%.1fsn" % (
            "✅" if rr.success else "⚠️", rr.status, rr.steps, rr.plan_steps,
            rr.replan_count, rr.tool_calls, ", ".join(rr.tools_used) or "-",
            rr.total_tokens, rr.elapsed_seconds))
        # Özet iki yere de: terminalde tek satırlık durum, dosyada tam cevapla.
        log.info("%s", ozet)
        slog.info("%s", ozet)
        slog.info("CEVAP: %s", _one(rr.answer, 2000))
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
        # TAM traceback hem terminale hem oturum dosyasına (teşhis için);
        # UI'a yalnızca kısa mesaj.
        log.exception("❌ HATA (%.1fsn sonra) — %s: %s",
                      time.time() - t0, type(exc).__name__, exc)
        slog.exception("❌ HATA (%.1fsn sonra) — %s: %s",
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
    _session_logger(body.thread_id).info(
        "🗑 BELLEK SIFIRLANDI (silinecek geçmiş var mıydı: %s)", removed)
    return {"ok": True, "removed": removed}


if __name__ == "__main__":
    # Doğrudan çalıştırma: `uv run python app.py` → http://127.0.0.1:8000
    # Port/host env'den de ayarlanabilir (PORT / HOST).
    import uvicorn

    uvicorn.run(app, host=os.getenv("HOST", "127.0.0.1"),
                port=int(os.getenv("PORT", "8000")))
