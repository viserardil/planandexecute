"""AQS (Agent Kalite Skoru) hesaplayıcı — çıktı KALİTESİNİ ölçer.

`success=True` yalnızca "cevap üretildi" demek; bu betik cevabın gerçekten DOĞRU
olup olmadığını dataset'in altın-anahtarlarıyla ölçer. Altı boyutu birleştirir:

  Puanlanan (ağırlıklı toplam → çekirdek):
    - içerik isabeti (reference_key_points)      w=0.35   [LLM-judge]
    - araç seçimi   (expected_tools recall)       w=0.25   [objektif]
    - gerçeğe dayanma (tool çıktısına dayanma)     w=0.20   [LLM-judge]
    - görev tamamlama (isteneni yaptı mı)          w=0.20   [LLM-judge]
  Kapılar (çarpan — "cevabı bozan" boyutlar):
    - halüsinasyon: none 1.0 / minor 0.5 / critical 0.2   [judge_hints.failure_signals]
    - dil: doğru 1.0 / yanlış 0.7                          [query_language]

  AQS = 100 × çekirdek × halüsinasyon_kapısı × dil_kapısı      (0–100)

Halüsinasyon, cevabın ajanın KENDİ araç gözlemlerine dayanıp dayanmadığına göre
ölçülür (canlı veride dış gerçek doğrulanamaz). Bu yüzden mümkünse ilgili
`_schema.json`'dan tool çıktıları (gözlemler) judge'a verilir.

Judge olarak agent'tan FARKLI bir model kullanılır (öz-yanlılığı azaltmak için;
varsayılan gpt-4o). Şema-bağımsız: herhangi bir results_*.json'u skorlar.

Kullanım:
    uv run python test/score.py test/results/results_test2.json --pace 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

DATASET = "sccaglayanworkacc/equity-research-agentic-eval"

ALIAS = {
    "get_current_stock_price": "get_stock_price",
    "get_historical_stock_prices": "get_historical_prices",
    "plot_chart": "visualize_data",
}
W = {"content": 0.35, "tool": 0.25, "grounding": 0.20, "task": 0.20}
HALLUC_GATE = {"none": 1.0, "minor": 0.5, "critical": 0.2}


def _verdict_model():
    from pydantic import BaseModel, Field

    class Verdict(BaseModel):
        key_points_covered: float = Field(description="Beklenen anahtar noktaların kapsanma oranı (0-1).")
        grounding: float = Field(description="Cevabın ARAÇ GÖZLEMLERİNE (uydurma değil) dayanma derecesi (0-1).")
        task_completed: float = Field(description="İstenen görevin gerçekten yapılma derecesi (0-1). Görselleştirmede gerçek grafik=1, sadece kod/metin=0.")
        hallucination: str = Field(description="'none' (temiz), 'minor' (gözlemde olmayan dayanaksız iddia) veya 'critical' (gözlemle çelişen ya da araçsız uydurulan olgu, yanlış varlık/ret).")
        failure_signals: list[str] = Field(default_factory=list, description="judge_hints.failure_signals'tan tetiklenenler.")
        language_ok: bool = Field(description="Cevap sorunun dilinde mi (query_language).")
        rationale: str = Field(description="Kısa gerekçe.")

    return Verdict


JUDGE_SYS = (
    "Sen tarafsız bir değerlendirme jürisisin. Bir finans-araştırma ajanının CEVABINI, "
    "sağlanan altın-anahtarlara ve ajanın ARAÇ GÖZLEMLERİNE göre puanlarsın. Canlı veriler "
    "değiştiği için dış gerçeği arama; cevabın ajanın KENDİ gözlemlerine dayanıp dayanmadığına bak. "
    "Bir iddia hiçbir gözlemde yoksa 'minor'; gözlemle çelişiyorsa ya da ilgili araç hiç "
    "çağrılmadan olgu uydurulmuşsa 'critical'. Yanlı olma, cevabı hangi sistemin ürettiğini "
    "bilmiyorsun. Alanları dürüstçe doldur."
)


def _judge_chain(model, api_key, base_url):
    from langchain_openai import ChatOpenAI
    from langchain_core.prompts import ChatPromptTemplate

    llm = ChatOpenAI(model=model, api_key=api_key, base_url=base_url,
                     temperature=0, max_tokens=700, timeout=90, max_retries=6)
    prompt = ChatPromptTemplate.from_messages([
        ("system", JUDGE_SYS),
        ("user",
         "SORU ({lang}):\n{query}\n\n"
         "AJANIN CEVABI:\n{answer}\n\n"
         "AJANIN ÇAĞIRDIĞI ARAÇLAR: {tools}\n\n"
         "ARAÇ GÖZLEMLERİ (toolların döndürdüğü — grounding'i buna göre değerlendir):\n{observations}\n\n"
         "BEKLENEN ANAHTAR NOKTALAR:\n{key_points}\n\n"
         "JÜRİ İPUÇLARI (strict_requirements / failure_signals / lenient_on / grading_notes):\n{hints}\n\n"
         "Bu cevabı yukarıdaki şemaya göre puanla."),
    ])
    return prompt | llm.with_structured_output(_verdict_model())


def tool_recall(used, expected, alternatives):
    if not expected:
        return None
    used_c = {ALIAS.get(t, t) for t in used}
    hit = len(used_c & (set(expected) | set(alternatives)))
    return min(1.0, hit / len(expected))


def _parse(row):
    def j(field, default):
        try:
            return json.loads(row[field])
        except Exception:
            return default
    exp = [t.get("tool") for t in j("expected_tools", []) if isinstance(t, dict) and t.get("tool")]
    alts = []
    for a in j("acceptable_tool_alternatives", []):
        if isinstance(a, dict):
            alts += a.get("tools", [])
    rkp = j("reference_key_points", {})
    kp = rkp.get("content", []) if isinstance(rkp, dict) else []
    jh = j("judge_hints", {})
    hints = {k: jh.get(k) for k in ("strict_requirements", "failure_signals", "lenient_on", "grading_notes") if isinstance(jh, dict) and jh.get(k)}
    return dict(exp=exp, alts=alts, kp=kp, hints=hints, lang=row.get("query_language", ""), cat=row.get("category", ""), tier=row.get("tier", ""))


def _load_observations(results_path):
    """İlgili _schema.json'dan case_id -> [(araç, çıktı)] gözlemlerini çıkarır."""
    schema_path = results_path.with_name(results_path.stem + "_schema.json")
    if not schema_path.exists():
        return {}
    try:
        docs = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    obs = {}
    for doc in docs if isinstance(docs, list) else []:
        cid = doc.get("case_id")
        pairs = []
        for ag in doc.get("agents", []):
            for st in ag.get("steps", []):
                for tc in st.get("tool_calls", []) or []:
                    out = tc.get("tool_output")
                    if out:
                        pairs.append((tc.get("tool_name", "?"), str(out)[:500]))
        obs[cid] = pairs
    return obs


def _clip01(x):
    try:
        return max(0.0, min(1.0, float(x)))
    except Exception:
        return 0.0


def compute_aqs(v, tool_r):
    content, grounding, task = _clip01(v.key_points_covered), _clip01(v.grounding), _clip01(v.task_completed)
    dims = {"content": (content, W["content"]), "grounding": (grounding, W["grounding"]), "task": (task, W["task"])}
    if tool_r is not None:
        dims["tool"] = (tool_r, W["tool"])
    core = sum(val * w for val, w in dims.values()) / sum(w for _, w in dims.values())
    hg = HALLUC_GATE.get((v.hallucination or "none").lower(), 1.0)
    lg = 1.0 if v.language_ok else 0.7
    return round(100 * core * hg * lg, 1), dict(content=content, tool=tool_r, grounding=grounding, task=task,
                                                hallucination=v.hallucination, halluc_gate=hg, language_ok=v.language_ok, lang_gate=lg)


def main():
    ap = argparse.ArgumentParser(description="AQS (Agent Kalite Skoru) hesaplayıcı.")
    ap.add_argument("results", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--judge-model", default="gpt-4o")
    ap.add_argument("--pace", type=float, default=3)
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    from plan_execute.config import settings
    from datasets import load_dataset

    d = json.loads(args.results.read_text(encoding="utf-8"))
    results = d["results"] if isinstance(d, dict) else d
    label = (d.get("test_name") if isinstance(d, dict) else None) or args.results.stem
    obs_map = _load_observations(args.results)

    gold = {r["case_id"]: _parse(r) for r in load_dataset(DATASET)["test"]}
    chain = _judge_chain(args.judge_model, settings.api_key, settings.base_url)

    scored = []
    items = results if args.limit is None else results[: args.limit]
    for i, r in enumerate(items, 1):
        cid = r["case_id"]
        g = gold.get(cid)
        ans = r.get("answer")
        if not g:
            continue
        if not ans:
            scored.append(dict(case_id=cid, tier=g["tier"], aqs=0.0, status=r.get("status"), note="cevap yok"))
            print(f"[{i}/{len(items)}] {cid}  AQS=0 (cevap yok)")
            continue
        obs = obs_map.get(cid, [])
        obs_txt = "\n".join(f"[{n}] {o}" for n, o in obs) or "(gözlem yok — araç çağrılmamış)"
        try:
            v = chain.invoke(dict(lang=g["lang"], query=r.get("prompt", ""), answer=str(ans),
                                  tools=", ".join(r.get("tools_used", []) or []) or "(yok)",
                                  observations=obs_txt,
                                  key_points="\n".join(f"- {k}" for k in g["kp"]) or "(belirtilmemiş)",
                                  hints=json.dumps(g["hints"], ensure_ascii=False)))
            tr = tool_recall(r.get("tools_used", []) or [], g["exp"], g["alts"])
            aqs, sub = compute_aqs(v, tr)
            scored.append(dict(case_id=cid, tier=g["tier"], cat=g["cat"], aqs=aqs, **sub,
                               failure_signals=v.failure_signals, rationale=v.rationale))
            print(f"[{i}/{len(items)}] {cid:5} AQS={aqs:5.1f}  içerik={sub['content']:.2f} "
                  f"araç={('%.2f' % tr) if tr is not None else 'NA'} ground={sub['grounding']:.2f} "
                  f"görev={sub['task']:.2f} halüs={v.hallucination}")
        except Exception as exc:
            scored.append(dict(case_id=cid, tier=g["tier"], aqs=None, error=str(exc)))
            print(f"[{i}/{len(items)}] {cid}  JUDGE HATASI: {exc}", file=sys.stderr)
        if args.pace and i < len(items):
            time.sleep(args.pace)

    valid = [s for s in scored if isinstance(s.get("aqs"), (int, float))]
    agg = {"label": label, "judge_model": args.judge_model, "n": len(valid),
           "avg_aqs": round(sum(s["aqs"] for s in valid) / len(valid), 1) if valid else 0}
    out = args.out or args.results.with_name(f"scores_{args.results.stem.replace('results_', '')}.json")
    out.write_text(json.dumps({"aggregate": agg, "cases": scored}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nOrtalama AQS ({label}): {agg['avg_aqs']}  (N={agg['n']})")
    print(f"Kaydedildi: {out}")


if __name__ == "__main__":
    main()
