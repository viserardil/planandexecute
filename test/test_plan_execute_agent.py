"""Plan-Execute ajanı için CANLI regresyon ve performans testleri.

ReAct projesindeki ``test/test_react_agent.py`` ile BİREBİR simetriktir: AYNI 10
KKB görevi, AYNI ilerleme takibi (JSONL) ve AYNI sonuç şeması (JSON) üretilir.
Böylece iki mimarinin çıktıları doğrudan (harmanlama yapmadan) karşılaştırılabilir.

Çalıştırma (pytest):
    set HF_TOKEN=<huggingface_anahtari>     # .env'den de okunur
    pytest test/test_plan_execute_agent.py -q

    HF_TOKEN yoksa testler atlanır (skip), başarısız olmaz.

Doğrudan çalıştırma (CLI, metrik toplama):
    python test/test_plan_execute_agent.py --version pe-v1
    python test/test_plan_execute_agent.py --case-id 3        # tek görev
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

# .env dosyasını erkenden yükle ki HF_TOKEN hem pytest hem CLI kipinde okunsun.
try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

# Proje kökünü ve src/ dizinini import edilebilir yap.
ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
for path in (ROOT_DIR, SRC_DIR):
    if str(path) not in sys.path:
        sys.path.append(str(path))

DEFAULT_OUTPUT_DIR = ROOT_DIR / "scratch" / "plan_execute_runs"
LOGGER = logging.getLogger("plan_execute_regression")


def has_hf_token() -> bool:
    return bool(os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACEHUB_API_TOKEN"))


# --- Görevler ---------------------------------------------------------------


@dataclass(frozen=True)
class PlanExecuteCase:
    id: int
    title: str
    prompt: str


# KKB görev seti — ReAct test'iyle BİREBİR aynı 10 prompt (adil karşılaştırma).
CASES: list[PlanExecuteCase] = [
    PlanExecuteCase(
        id=1,
        title="KKB 2025 cirosu (canlı web)",
        prompt="Kredi Kayıt Bürosunun 2025 yılı cirosu nedir?",
    ),
    PlanExecuteCase(
        id=2,
        title="Yönetim kurulu (canlı web)",
        prompt="Kredi Kayıt Bürosunun Yönetim Kurulunda kaç kişi vardır ve isimleri nedir?",
    ),
    PlanExecuteCase(
        id=3,
        title="Ardıldeks skoru — SQL + formül + calculator kompozisyonu",
        prompt="Can Öztürk'ün Ardıldeks skoru kaçtır?",
    ),
    PlanExecuteCase(
        id=4,
        title="Ürün sayısı (canlı web)",
        prompt="KKB'nin toplam kaç ürünü vardır ve isimleri nelerdir?",
    ),
    PlanExecuteCase(
        id=5,
        title="Muhakeme: aynı borçta farklı puan nedenleri",
        prompt=(
            "Kredi notunda aynı toplam borç tutarına sahip iki kişinin farklı "
            "puan almasının temel nedenleri nelerdir?"
        ),
    ),
    PlanExecuteCase(
        id=6,
        title="Muhakeme: yüksek not ama başvuru reddi",
        prompt=(
            "Kredi notu yüksek olmasına rağmen kredi başvurusu reddedilen bir "
            "müşterinin karşılaşabileceği olası nedenleri önem sırasına göre açıkla."
        ),
    ),
    PlanExecuteCase(
        id=7,
        title="Bankalar dışı sektörler (canlı web)",
        prompt="KKB'nin bankalar dışındaki hangi sektörlerle veri paylaşımı veya iş birliği bulunmaktadır?",
    ),
    PlanExecuteCase(
        id=8,
        title="Sertifikalar (canlı web)",
        prompt="KKB hangi sertifikalara (örneğin ISO 27001, ISO 22301, ISO 27701 vb.) sahiptir?",
    ),
    PlanExecuteCase(
        id=9,
        title="Uluslararası iş birlikleri (canlı web)",
        prompt="KKB'nin uluslararası iş birlikleri veya yabancı ortak projeleri nelerdir?",
    ),
    PlanExecuteCase(
        id=10,
        title="Anadolu Veri Merkezi yılı (canlı web)",
        prompt="KKB'nin Anadolu Veri Merkezi hangi yılda %100 kapasiteye ulaşmıştır?",
    ),
]


def get_cases() -> list[PlanExecuteCase]:
    return list(CASES)


# --- Yardımcılar ------------------------------------------------------------


def configure_logging(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "plan_execute_tests.log"

    LOGGER.setLevel(logging.INFO)
    LOGGER.handlers.clear()
    LOGGER.addHandler(logging.FileHandler(log_path, mode="w", encoding="utf-8"))
    LOGGER.addHandler(logging.StreamHandler(sys.stdout))

    for handler in LOGGER.handlers:
        handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _one_line(value: Any, limit: int = 240) -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _ensure_stdout_utf8() -> None:
    if sys.platform == "win32":
        try:
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        except Exception:
            pass


def _slugify(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^a-z0-9_\-]", "", name)
    return name.strip("_-")


def prompt_version(default_prefix: str = "test") -> str:
    fallback = f"{default_prefix}_{time.strftime('%Y%m%d_%H%M%S')}"
    try:
        raw = input(f"Bu test koşusu için bir isim gir [{fallback}]: ")
    except EOFError:
        raw = ""
    slug = _slugify(raw)
    return slug or fallback


# --- İlerleme takibi (JSONL olay akışı + terminal logu) ---------------------


class RunTracker:
    """Canlı terminal ilerlemesi ve makine-okur JSONL olay akışı üretir."""

    def __init__(self, output_dir: Path, version_tag: str, enabled: bool = True):
        self.output_dir = output_dir
        self.version_tag = version_tag
        self.enabled = enabled
        self.progress_path = output_dir / f"test_progress_{version_tag}.jsonl"
        self.started_at = time.time()

    def start_run(self, total: int, case_ids: list[int] | None = None) -> None:
        if not self.enabled:
            return
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_path.write_text("", encoding="utf-8")
        LOGGER.info("[RUN] version=%s total=%s progress=%s", self.version_tag, total, self.progress_path)
        self._write_event({"event": "run_start", "version": self.version_tag, "total": total, "case_ids": case_ids or []})

    def start_case(self, index: int | None, total: int | None, case: PlanExecuteCase) -> None:
        if not self.enabled:
            return
        LOGGER.info(
            "[CASE] %s id=%s title=%s prompt=%s",
            self._case_prefix(index, total),
            case.id,
            _one_line(case.title, 90),
            _one_line(case.prompt, 160),
        )
        self._write_event(
            {
                "event": "case_start",
                "index": index,
                "total": total,
                "id": case.id,
                "title": case.title,
                "prompt": case.prompt,
            }
        )

    def finish_case(self, index: int | None, total: int | None, result: dict[str, Any]) -> None:
        if not self.enabled:
            return
        status = "ERROR" if result.get("error") else ("SUCCESS" if result.get("success") else "NO_ANSWER")
        LOGGER.info(
            "[DONE] %s id=%s status=%s llm_calls=%s plan_steps=%s tool_calls=%s tokens=%s duration_ms=%s",
            self._case_prefix(index, total),
            result.get("id"),
            status,
            result.get("llm_calls", 0),
            result.get("plan_steps", 0),
            result.get("tool_calls", 0),
            result.get("total_tokens", 0),
            result.get("duration_ms", 0),
        )
        if result.get("tools_used"):
            LOGGER.info("[TOOLS] id=%s %s", result.get("id"), ", ".join(result["tools_used"]))
        if result.get("answer"):
            LOGGER.info("[ANSWER] id=%s %s", result.get("id"), _one_line(result.get("answer"), 260))
        if result.get("error"):
            LOGGER.error("[ERROR] id=%s %s", result.get("id"), result.get("error"))

        self._write_event(
            {
                "event": "case_finish",
                "index": index,
                "total": total,
                "id": result.get("id"),
                "success": result.get("success", False),
                "steps": result.get("steps", 0),
                "llm_calls": result.get("llm_calls", 0),
                "plan_steps": result.get("plan_steps", 0),
                "replan_count": result.get("replan_count", 0),
                "tool_calls": result.get("tool_calls", 0),
                "tools_used": result.get("tools_used", []),
                "input_tokens": result.get("input_tokens", 0),
                "output_tokens": result.get("output_tokens", 0),
                "total_tokens": result.get("total_tokens", 0),
                "duration_ms": result.get("duration_ms", 0),
                "answer_preview": _one_line(result.get("answer"), 500),
                "error": result.get("error"),
            }
        )

    def finish_run(self, completed: int, total: int, failures: int, output_path: Path) -> None:
        if not self.enabled:
            return
        duration_s = round(time.time() - self.started_at, 2)
        LOGGER.info(
            "[SUMMARY] completed=%s/%s failures=%s duration_s=%s results=%s progress=%s",
            completed,
            total,
            failures,
            duration_s,
            output_path,
            self.progress_path,
        )
        self._write_event(
            {
                "event": "run_finish",
                "completed": completed,
                "total": total,
                "failures": failures,
                "duration_s": duration_s,
                "results_path": str(output_path),
            }
        )

    def _case_prefix(self, index: int | None, total: int | None) -> str:
        if index is None or total is None:
            return "[?/?]"
        return f"[{index}/{total}]"

    def _write_event(self, event: dict[str, Any]) -> None:
        event = {"timestamp": _now_iso(), **event}
        with self.progress_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


# --- Trace serileştirme -----------------------------------------------------


def serialize_trace(trace: list) -> list[dict[str, Any]]:
    """Yürütülen plan adımlarını (adım metni + executor sonucu) JSON'a yazılabilir
    hale getirir — plan-execute'un nasıl ilerlediğini sonuçlarda görebilmek için."""
    steps: list[dict[str, Any]] = []
    for i, step in enumerate(trace, 1):
        steps.append(
            {
                "adim": i,
                "step": getattr(step, "step", None),
                "result": getattr(step, "result", None),
            }
        )
    return steps


# --- Canlı ajan çalıştırıcı -------------------------------------------------


class PlanExecuteTestRunner:
    def __init__(
        self,
        version_tag: str = "default",
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        recursion_limit: int = 50,
        save_each: bool = True,
        progress_enabled: bool = True,
    ):
        from plan_execute.config import settings

        self.version_tag = version_tag
        self.output_dir = output_dir
        self.recursion_limit = recursion_limit
        self.model = settings.model_name
        self.save_each = save_each
        self.results: list[dict[str, Any]] = []
        self.tracker = RunTracker(output_dir=output_dir, version_tag=version_tag, enabled=progress_enabled)

    def run_case(self, case: PlanExecuteCase, index: int | None = None, total: int | None = None) -> dict[str, Any] | None:
        self.tracker.start_case(index, total, case)
        LOGGER.info("")
        LOGGER.info("%s TEST %s %s", "=" * 25, case.title, "=" * 25)
        LOGGER.info("PROMPT: %s", case.prompt)

        from plan_execute.agent import PlanExecuteAgent

        test_result: dict[str, Any] = {
            "id": case.id,
            "title": case.title,
            "prompt": case.prompt,
            "success": False,
            "answer": None,
            "error": None,
            "steps": 0,
            "llm_calls": 0,
            "plan_steps": 0,
            "replan_count": 0,
            "tool_calls": 0,
            "tools_used": [],
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "duration_ms": 0,
            "trace": [],
        }

        try:
            agent = PlanExecuteAgent(recursion_limit=self.recursion_limit, verbose=False)
            LOGGER.info("Canlı Plan-Execute ajanı çalıştırılıyor (model=%s)...", self.model)
            run_result = agent.run(case.prompt)

            test_result.update(
                {
                    "success": run_result.success,
                    "answer": run_result.answer,
                    "steps": run_result.steps,
                    "llm_calls": run_result.llm_calls,
                    "plan_steps": run_result.plan_steps,
                    "replan_count": run_result.replan_count,
                    "tool_calls": run_result.tool_calls,
                    "tools_used": run_result.tools_used,
                    "input_tokens": run_result.input_tokens,
                    "output_tokens": run_result.output_tokens,
                    "total_tokens": run_result.total_tokens,
                    "duration_ms": int(run_result.elapsed_seconds * 1000),
                    "trace": serialize_trace(run_result.trace),
                }
            )

            LOGGER.info("BAŞARILI: %s", run_result.success)
            LOGGER.info(
                "LLM ÇAĞRI: %s  PLAN ADIM: %s  REPLAN: %s  ARAÇ ÇAĞRI: %s",
                run_result.llm_calls,
                run_result.plan_steps,
                run_result.replan_count,
                run_result.tool_calls,
            )
            LOGGER.info(
                "TOKEN: total=%s (girdi=%s, çıktı=%s)",
                run_result.total_tokens,
                run_result.input_tokens,
                run_result.output_tokens,
            )
            LOGGER.info("CEVAP: %s", run_result.answer)

            self.results.append(test_result)
            self.tracker.finish_case(index, total, test_result)
            if self.save_each:
                self.save_json()
            return test_result
        except Exception as exc:
            LOGGER.error("TEST HATASI (%s): %s", case.title, exc, exc_info=True)
            test_result["error"] = str(exc)
            self.results.append(test_result)
            self.tracker.finish_case(index, total, test_result)
            if self.save_each:
                self.save_json()
            return None

    def save_json(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        output_path = self.output_dir / f"test_results_{self.version_tag}.json"
        tmp_path = output_path.with_suffix(".json.tmp")

        succeeded = [r for r in self.results if r.get("success")]
        payload = {
            "version": self.version_tag,
            "architecture": "plan-execute",
            "model": self.model,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "total_tests": len(self.results),
            "succeeded": len(succeeded),
            "avg_llm_calls": round(sum(r["llm_calls"] for r in self.results) / len(self.results), 2) if self.results else 0,
            "avg_plan_steps": round(sum(r["plan_steps"] for r in self.results) / len(self.results), 2) if self.results else 0,
            "avg_tool_calls": round(sum(r["tool_calls"] for r in self.results) / len(self.results), 2) if self.results else 0,
            "avg_total_tokens": round(sum(r["total_tokens"] for r in self.results) / len(self.results), 1)
            if self.results
            else 0,
            "avg_duration_ms": round(sum(r["duration_ms"] for r in self.results) / len(self.results), 1)
            if self.results
            else 0,
            "results": self.results,
        }
        tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(output_path)
        LOGGER.info("Yapısal sonuçlar kaydedildi: %s", output_path)
        return output_path


# ===========================================================================
# Canlı testler (gerçek HuggingFace modeline karşı)
# ===========================================================================


@pytest.mark.live
@pytest.mark.requires_llm
@pytest.mark.parametrize("case", get_cases(), ids=lambda case: f"{case.id}-{case.title}")
def test_plan_execute_agent_live(case: PlanExecuteCase, tmp_path: Path) -> None:
    if not has_hf_token():
        pytest.skip("HF_TOKEN tanımlı değil; canlı testler atlanıyor.")

    runner = PlanExecuteTestRunner(
        version_tag=os.environ.get("PLAN_EXECUTE_TEST_VERSION", "pytest"),
        output_dir=tmp_path,
        recursion_limit=int(os.environ.get("RECURSION_LIMIT", "50")),
        save_each=False,
    )
    result = runner.run_case(case)
    assert result is not None and result.get("error") is None, result and result.get("error")


# ===========================================================================
# Doğrudan çalıştırma (CLI) — canlı metrik toplama
# ===========================================================================


def main() -> int:
    _ensure_stdout_utf8()

    parser = argparse.ArgumentParser(description="Canlı Plan-Execute ajanı regresyon/performans testlerini çalıştır.")
    parser.add_argument("--version", default=None, help="Koşu etiketi (çıktı dosya adı). Verilmezse terminalde sorulur.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--recursion-limit", type=int, default=int(os.environ.get("RECURSION_LIMIT", "50")))
    parser.add_argument("--case-id", type=int, action="append", help="Yalnızca seçili görev id'sini çalıştır. Tekrarlanabilir.")
    parser.add_argument("--no-progress", action="store_true", help="Terminal ilerlemesi ve JSONL çıktısını kapat.")
    args = parser.parse_args()

    version = args.version or os.environ.get("PLAN_EXECUTE_TEST_VERSION") or prompt_version()

    configure_logging(args.output_dir)

    if not has_hf_token():
        LOGGER.error("HF_TOKEN tanımlı değil. .env dosyanıza HuggingFace anahtarınızı ekleyin.")
        return 1

    cases = get_cases()
    if args.case_id:
        wanted = set(args.case_id)
        cases = [case for case in cases if case.id in wanted]

    if not cases:
        LOGGER.warning("Çalıştırılacak görev bulunamadı.")
        return 1

    runner = PlanExecuteTestRunner(
        version_tag=version,
        output_dir=args.output_dir,
        recursion_limit=args.recursion_limit,
        progress_enabled=not args.no_progress,
    )
    LOGGER.info("PLAN-EXECUTE TEST RUNNER - '%s' - %s görev (model=%s)", version, len(cases), runner.model)
    runner.tracker.start_run(total=len(cases), case_ids=[case.id for case in cases])

    errors = 0
    try:
        for index, case in enumerate(cases, 1):
            LOGGER.info("[%s/%s] Sıradaki test başlıyor...", index, len(cases))
            result = runner.run_case(case, index=index, total=len(cases))
            if result is None or result.get("error"):
                errors += 1
    except KeyboardInterrupt:
        LOGGER.info("Test çalıştırması kullanıcı tarafından durduruldu.")
    finally:
        output_path = runner.save_json()
        runner.tracker.finish_run(
            completed=len(runner.results),
            total=len(cases),
            failures=errors,
            output_path=output_path,
        )

    LOGGER.info("%s/%s görev çalıştı, %s hata. Kontrolleri JSON'dan yap: %s",
                len(runner.results), len(cases), errors, output_path)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
