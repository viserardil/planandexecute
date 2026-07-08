"""Executor ajanının kullanacağı araçlar (tools).

ADİL karşılaştırma için bu araçlar ReAct projesindeki ``src/tools.py`` ile
BİREBİR aynı davranışa sahiptir (aynı hesap makinesi, aynı Ardıldeks formülü,
aynı persona SQLite veritabanı, aynı Tavily + disk cache web araması). Tek fark:
burada executor bir LangGraph ``create_react_agent`` alt-ajanı olduğu için
araçlar LangChain ``@tool`` nesneleri olarak sarmalanır; iç mantık aynıdır.

Web araması sonuçları diske cache'lenir ve cache dizini ReAct projesinden
kopyalanmıştır; böylece iki mimari aynı sorguya aynı web gözlemini görür ve
tek değişken mimari kalır.
"""

from __future__ import annotations

import ast
import hashlib
import math
import operator
import os
import re
import sqlite3
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

# --- Güvenli hesap makinesi -------------------------------------------------
# eval() yerine AST tabanlı, yalnızca aritmetik ifadelere izin veren yorumlayıcı.

_ALLOWED_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_ALLOWED_FUNCS = {
    "sqrt": math.sqrt,
    "log": math.log,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "abs": abs,
    "round": round,
    "min": min,  # skoru alt sınıra kırpmak için: max(1, ...)
    "max": max,  # skoru üst sınıra kırpmak için: min(1900, ...)
}


def _safe_eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant):  # sayılar
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("yalnızca sayı sabitlerine izin var")
    if isinstance(node, ast.BinOp):
        return _ALLOWED_OPERATORS[type(node.op)](
            _safe_eval(node.left), _safe_eval(node.right)
        )
    if isinstance(node, ast.UnaryOp):
        return _ALLOWED_OPERATORS[type(node.op)](_safe_eval(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        fn = _ALLOWED_FUNCS.get(node.func.id)
        if fn is None:
            raise ValueError(f"izin verilmeyen fonksiyon: {node.func.id}")
        return fn(*[_safe_eval(a) for a in node.args])
    raise ValueError("izin verilmeyen ifade")


def _calculator(expression: str) -> str:
    tree = ast.parse(expression.strip(), mode="eval")
    result = _safe_eval(tree.body)
    return f"{result}"


# --- Ardıldeks skor formülü -------------------------------------------------
# Gerçek KKB firma bilgisi burada TUTULMAZ; o sorular canlı web_arama ile
# cevaplanır. Burada yalnızca Ardıldeks skorunun (sentetik, bize ait bir indeks)
# hesaplama tanımı var.

_KKB_BILGI: list[dict[str, Any]] = [
    {
        "anahtarlar": ["ardildeks", "ardıldeks", "skor formülü", "skor nasıl", "not hesaplama", "algoritma"],
        "cevap": (
            "Ardıldeks skoru (1-1900) şu formülle hesaplanır:\n"
            "Skor = 300 + odeme_gecmisi*12 + (1 - borc_kullanim_orani)*250 "
            "+ en_eski_hesap_yili*15 - karsiliksiz_cek*200 - aktif_kredi_sayisi*10\n"
            "Sonuç 1 ile 1900 arasına kırpılır (clamp). Bileşenler 'personalar' "
            "tablosundaki sütunlardır; kişiye ait değerleri sql_sorgu ile çekip "
            "calculator ile hesapla."
        ),
    },
]


def _kkb_bilgi(query: str) -> str:
    q = query.lower().strip()
    words = [w for w in re.findall(r"[\wçğıöşü]+", q) if len(w) > 2]

    best = None
    best_score = 0
    for entry in _KKB_BILGI:
        keys = " ".join(entry["anahtarlar"]).lower()
        score = sum(1 for w in words if w in keys)
        # tam anahtar ifadesi geçiyorsa güçlü eşleşme
        score += sum(2 for k in entry["anahtarlar"] if k in q)
        if score > best_score:
            best_score = score
            best = entry

    if best is None or best_score == 0:
        return "Bu konuda KKB bilgi tabanında kayıt yok."
    return best["cevap"]


# --- KKB persona veritabanı (SQLite) ---------------------------------------
# Ardıldeks ham bileşenleri burada; skor SAKLANMAZ, hesaplattırılır.

_PERSONALAR: list[dict[str, Any]] = [
    {"id": 1, "ad": "Ardıl Deniz Sustam", "odeme_gecmisi": 95, "borc_kullanim_orani": 0.20,
     "karsiliksiz_cek": 0, "aktif_kredi_sayisi": 2, "en_eski_hesap_yili": 8,
     "toplam_borc": 145000, "gecikmis_borc": 0},
    {"id": 2, "ad": "Zeynep Kaya", "odeme_gecmisi": 70, "borc_kullanim_orani": 0.65,
     "karsiliksiz_cek": 1, "aktif_kredi_sayisi": 4, "en_eski_hesap_yili": 5,
     "toplam_borc": 320000, "gecikmis_borc": 45000},
    {"id": 3, "ad": "Mehmet Demir", "odeme_gecmisi": 88, "borc_kullanim_orani": 0.30,
     "karsiliksiz_cek": 0, "aktif_kredi_sayisi": 1, "en_eski_hesap_yili": 10,
     "toplam_borc": 60000, "gecikmis_borc": 0},
    {"id": 4, "ad": "Elif Yılmaz", "odeme_gecmisi": 45, "borc_kullanim_orani": 0.90,
     "karsiliksiz_cek": 3, "aktif_kredi_sayisi": 5, "en_eski_hesap_yili": 3,
     "toplam_borc": 510000, "gecikmis_borc": 120000},
    {"id": 5, "ad": "Can Öztürk", "odeme_gecmisi": 100, "borc_kullanim_orani": 0.05,
     "karsiliksiz_cek": 0, "aktif_kredi_sayisi": 3, "en_eski_hesap_yili": 15,
     "toplam_borc": 30000, "gecikmis_borc": 0},
]


def _build_persona_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE personalar ("
        "id INTEGER, ad TEXT, odeme_gecmisi INTEGER, borc_kullanim_orani REAL, "
        "karsiliksiz_cek INTEGER, aktif_kredi_sayisi INTEGER, en_eski_hesap_yili INTEGER, "
        "toplam_borc REAL, gecikmis_borc REAL)"
    )
    conn.executemany(
        "INSERT INTO personalar VALUES (:id, :ad, :odeme_gecmisi, :borc_kullanim_orani, "
        ":karsiliksiz_cek, :aktif_kredi_sayisi, :en_eski_hesap_yili, :toplam_borc, :gecikmis_borc)",
        _PERSONALAR,
    )
    conn.commit()
    return conn


def _sql_sorgu(query: str) -> str:
    q = query.strip().rstrip(";").strip()
    if not q.lower().startswith("select"):
        return "HATA: yalnızca SELECT sorgularına izin var."

    conn = _build_persona_db()
    try:
        cursor = conn.execute(q)
        rows = cursor.fetchall()
        headers = [d[0] for d in cursor.description]
    except Exception as exc:
        return f"HATA: SQL çalıştırılamadı: {exc}"
    finally:
        conn.close()

    if not rows:
        return "Sonuç bulunamadı."
    lines = [" | ".join(headers)]
    lines.extend(" | ".join(str(value) for value in row) for row in rows)
    return "\n".join(lines)


# --- İnternet araması (Tavily) ---------------------------------------------
# Benchmark tekrar üretilebilir olsun diye sonuçlar diske cache'lenir
# (WEB_CACHE=0 ile kapatılabilir). Cache dizini ReAct projesinden kopyalandığı
# için aynı sorgu iki mimaride de aynı çıktıyı verir.

_WEB_CACHE_DIR = Path(__file__).resolve().parents[2] / "scratch" / "web_cache"


def _web_cache_path(query: str) -> Path:
    digest = hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:16]
    return _WEB_CACHE_DIR / f"{digest}.txt"


def _web_arama(query: str) -> str:
    query = query.strip()
    if not query:
        return "HATA: boş arama sorgusu."

    use_cache = os.getenv("WEB_CACHE", "1") != "0"
    cache_path = _web_cache_path(query)
    if use_cache and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return "HATA: TAVILY_API_KEY tanımlı değil; internet araması yapılamıyor."

    try:
        from tavily import TavilyClient
    except ImportError:
        return "HATA: 'tavily' paketi kurulu değil (uv add tavily-python)."

    client = TavilyClient(api_key=api_key)
    response = client.search(query, max_results=5, topic="general", include_answer=True)

    parts: list[str] = []
    answer = response.get("answer")
    if answer:
        parts.append(f"Özet: {answer}")
    for item in response.get("results", []):
        title = (item.get("title") or "").strip()
        content = (item.get("content") or "").strip()
        url = item.get("url") or ""
        parts.append(f"- {title}: {content} ({url})")

    text = "\n".join(parts) if parts else "Arama sonucu bulunamadı."

    if use_cache:
        _WEB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(text, encoding="utf-8")
    return text


# --- LangChain araç sarmalları ---------------------------------------------
# İç mantık yukarıda; burada create_react_agent'ın kullanacağı @tool nesneleri.


@tool
def calculator(expression: str) -> str:
    """Aritmetik ifadeleri hesaplar. Girdi olarak bir matematik ifadesi al.
    Desteklenen: + - * / // % **, sqrt, log, sin, cos, tan, abs, round,
    min, max. Örnek girdi: min(1900, 300 + 95*12)"""
    try:
        return _calculator(expression)
    except Exception as exc:  # noqa: BLE001 - araç hatası ajana geri döner
        return f"HATA: {exc}"


@tool
def kkb_bilgi(query: str) -> str:
    """Ardıldeks skorunun hesaplama formülünü verir. Bir kişinin Ardıldeks
    skorunu hesaplarken bu formülü öğrenmek için kullan. KKB firması
    hakkındaki güncel/olgusal sorular için bunu DEĞİL web_arama'yı kullan.
    Girdi: 'ardildeks formülü'"""
    try:
        return _kkb_bilgi(query)
    except Exception as exc:  # noqa: BLE001
        return f"HATA: {exc}"


@tool
def sql_sorgu(query: str) -> str:
    """Persona kredi veritabanında salt-okunur SELECT çalıştırır.
    Tablo: personalar(id, ad, odeme_gecmisi, borc_kullanim_orani,
    karsiliksiz_cek, aktif_kredi_sayisi, en_eski_hesap_yili, toplam_borc,
    gecikmis_borc). Kişiye ait ham verileri çekmek için kullan.
    Örnek girdi: SELECT * FROM personalar WHERE ad LIKE '%Ardıl%'"""
    try:
        return _sql_sorgu(query)
    except Exception as exc:  # noqa: BLE001
        return f"HATA: {exc}"


@tool
def web_arama(query: str) -> str:
    """Bilgi tabanında olmayan güncel/gerçek dünya bilgisi için internette
    arama yapar. Girdi olarak arama sorgusu al. Örnek girdi: KKB 2025 cirosu"""
    try:
        return _web_arama(query)
    except Exception as exc:  # noqa: BLE001
        return f"HATA: {exc}"


def get_tools() -> list:
    """Ortak araç listesi — hem Plan-Execute hem ReAct AYNI seti kullanır.

    KKB benchmark araç seti: calculator, kkb_bilgi, sql_sorgu, web_arama.
    """
    return [calculator, kkb_bilgi, sql_sorgu, web_arama]
