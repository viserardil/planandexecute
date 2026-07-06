"""Executor ajanının kullanacağı araçlar.

Karşılaştırmanın adil olması için Plan-Execute ve ReAct AYNI araç setini kullanmalı.
Bu yüzden araçlar tek bir yerde tanımlanır.
"""

from __future__ import annotations

from langchain_community.tools import DuckDuckGoSearchRun
from langchain_core.tools import tool
from numexpr import evaluate


@tool
def calculator(expression: str) -> str:
    """Bir matematiksel ifadeyi hesaplar.

    Örn: '2 * (3 + 4)', 'sqrt(144)', '15% of 200' yerine '0.15 * 200'.
    Girdi geçerli bir Python/numexpr aritmetik ifadesi olmalıdır.
    """
    try:
        result = evaluate(expression)
        return str(result)
    except Exception as exc:  # noqa: BLE001 - araç hatası ajana geri döner
        return f"Hesaplama hatası: {exc}"


search = DuckDuckGoSearchRun(
    name="web_search",
    description="Güncel bilgi veya bilinmeyen olgular için web'de arama yapar.",
)


def get_tools() -> list:
    """Ortak araç listesi — hem Plan-Execute hem ReAct bunu kullanır."""
    return [search, calculator]
