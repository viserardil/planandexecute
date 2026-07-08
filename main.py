"""Plan-Execute ajanını çalıştıran giriş noktası.

ReAct projesindeki main.py ile simetrik: aynı örnek görevler, aynı metrik özeti.

Kullanım:
    uv run python main.py                         # örnek görevleri çalıştır
    uv run python main.py "Can Öztürk'ün Ardıldeks skoru kaçtır?"  # tek soru
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# src/ layout: paket kurulu değilse de import edilebilsin.
SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from plan_execute.agent import PlanExecuteAgent, RunResult  # noqa: E402

# Örnek görev seti — ReAct main.py ile aynı görevler (adil karşılaştırma).
SAMPLE_TASKS = [
    "KKB'nin toplam kaç ürünü vardır ve isimleri nelerdir?",
    "Ardıl'ın Ardıldeks skoru kaçtır?",
    "KKB'nin Anadolu Veri Merkezi hangi yılda %100 kapasiteye ulaşmıştır?",
]


def _print_metrics(r: RunResult) -> None:
    print("\n" + "=" * 60)
    print(f"Soru       : {r.question}")
    print(f"Cevap      : {r.answer}")
    print(f"Başarılı   : {r.success}")
    print(f"LLM çağrısı: {r.llm_calls}  (steps)")
    print(f"Plan adımı : {r.plan_steps}   Replan: {r.replan_count}")
    print(f"Araç çağrı : {r.tool_calls}  {r.tools_used}")
    print(f"Token      : {r.input_tokens} girdi + {r.output_tokens} çıktı "
          f"= {r.total_tokens}")
    print(f"Süre       : {r.elapsed_seconds:.2f} sn")
    print("=" * 60)


def main() -> None:
    load_dotenv()
    if not (os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")):
        sys.exit(
            "HF_TOKEN tanımlı değil. .env dosyasına HuggingFace API anahtarını "
            "HF_TOKEN=<anahtar> olarak ekle."
        )

    recursion_limit = int(os.getenv("RECURSION_LIMIT", "50"))
    agent = PlanExecuteAgent(recursion_limit=recursion_limit, verbose=True)

    tasks = [" ".join(sys.argv[1:])] if len(sys.argv) > 1 else SAMPLE_TASKS

    results: list[RunResult] = []
    for task in tasks:
        result = agent.run(task)
        _print_metrics(result)
        results.append(result)

    if len(results) > 1:
        print("\nÖZET")
        print(f"  Toplam görev  : {len(results)}")
        print(f"  Başarılı      : {sum(r.success for r in results)}")
        print(f"  Ort. LLM çağrı: {sum(r.llm_calls for r in results) / len(results):.1f}")
        print(f"  Ort. token    : {sum(r.total_tokens for r in results) / len(results):.0f}")
        print(f"  Ort. süre     : {sum(r.elapsed_seconds for r in results) / len(results):.2f} sn")


if __name__ == "__main__":
    main()
