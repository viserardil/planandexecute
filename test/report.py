"""Eval çıktısını (results_<sürüm>_schema.json) OKUNASI, satır-kaydırmasız bir
metin raporuna çevirir: uzun tool çıktıları ve cevaplar ~genişlik karakterde
gerçek satır sonlarıyla sarılır (yatay kaydırma yok).

Kullanım:
    uv run python test/report.py --version canli_test
    uv run python test/report.py --file test/results/results_x_schema.json --width 90
Çıktı: test/results/report_<sürüm>.md  (VSCode'da açıp okuyabilir/önizleyebilirsin)
"""

from __future__ import annotations

import argparse
import json
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "test" / "results"


def wrap(text, width: int, indent: str = "    ") -> str:
    """Metni verilen genişlikte sarar; içindeki mevcut satır sonlarını korur."""
    text = "" if text is None else str(text)
    out: list[str] = []
    for para in (text.splitlines() or [""]):
        if not para.strip():
            out.append("")
            continue
        out.append(textwrap.fill(
            para, width=width,
            initial_indent=indent, subsequent_indent=indent,
            break_long_words=True, break_on_hyphens=False,
        ))
    return "\n".join(out)


def render(objs: list[dict], width: int) -> str:
    lines: list[str] = []
    for r in objs:
        fs = r.get("framework_specific") or {}
        steps = (r.get("agents") or [{}])[0].get("steps", [])
        toolcalls = [tc for s in steps for tc in (s.get("tool_calls") or [])]
        tokens = r.get("tokens") or {}
        latency = r.get("latency") or {}

        lines.append("═" * (width + 4))
        lines.append(
            f"[{r.get('case_id')}]  {r.get('status')}  "
            f"steps={r.get('total_steps')}  plan={fs.get('plan_steps')}  "
            f"tool_calls={len(toolcalls)}  tokens={tokens.get('total')}  "
            f"süre={round((latency.get('total_ms') or 0) / 1000, 1)}sn"
        )
        lines.append("PROMPT:")
        lines.append(wrap(r.get("prompt"), width))

        plan = fs.get("plan_trace") or []
        if plan:
            lines.append("PLAN:")
            for i, p in enumerate(plan, 1):
                lines.append(wrap(f"{i}. {p.get('step')}", width))

        if toolcalls:
            lines.append("ARAÇLAR:")
            for i, tc in enumerate(toolcalls, 1):
                inp = (tc.get("tool_input") or {}).get("input", "")
                ok = "OK" if tc.get("success") else "HATA"
                lines.append(f"  {i}. {tc.get('tool_name')}({inp})  [{ok}]  {round(tc.get('latency_ms', 0))}ms")
                lines.append(wrap(tc.get("tool_output"), width, indent="       "))

        lines.append("CEVAP:")
        lines.append(wrap(r.get("final_answer"), width))
        if r.get("error_info"):
            lines.append("HATA:")
            lines.append(wrap(json.dumps(r["error_info"], ensure_ascii=False), width))
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser(description="Eval schema JSON'unu okunası metin raporuna çevirir.")
    p.add_argument("--version", help="results_<sürüm>_schema.json okur.")
    p.add_argument("--file", type=Path, help="Doğrudan bir schema/summary JSON yolu.")
    p.add_argument("--width", type=int, default=100, help="Satır genişliği (varsayılan 100).")
    args = p.parse_args()

    if args.file:
        path = args.file
    elif args.version:
        path = RESULTS / f"results_{args.version}_schema.json"
    else:
        print("--version ya da --file ver.", file=sys.stderr)
        return 1
    if not path.exists():
        print(f"Dosya yok: {path}", file=sys.stderr)
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    objs = data.get("results", [data]) if isinstance(data, dict) else data

    ver = args.version or path.stem.replace("results_", "").replace("_schema", "")
    out = RESULTS / f"report_{ver}.md"
    out.write_text(render(objs, args.width), encoding="utf-8")
    print(f"Rapor yazıldı: {out}  ({len(objs)} vaka, genişlik {args.width})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
