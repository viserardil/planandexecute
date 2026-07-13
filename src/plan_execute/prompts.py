"""Planner ve replanner için prompt şablonları."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

PLANNER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Sen bir görev planlayıcısısın. Görevi analiz edip kararını üç alanla ver:\n"
            "- needs_tools: Görev güncel/olgusal veri, hesaplama ya da araç gerektiriyor "
            "mu? Fiyat, temel veriler, finansal rasyolar, gelir tablosu, analist görüşü, "
            "teknik gösterge, haber, web araması, grafik, hesaplama → true. "
            "Kapsam dışı istekler (emir/işlem verme, alım-satım yapma, kişisel yatırım "
            "tavsiyesi), selamlaşma/sohbet ya da gerçekten muğlak olup netleştirme "
            "gereken sorular → false.\n"
            "- steps: needs_tools=true ise doğru sırada, ayrı ayrı yürütülebilir plan "
            "adımları koy (gereksiz adım ekleme; basit tek-araçlı sorularda tek adım "
            "yeterli; her adım gereken tüm bilgiyi içersin). needs_tools=false ise boş "
            "bırak. ASLA hafızandan fiyat/veri uydurma — olgusal her şey için adım koy.\n"
            "- direct_answer: needs_tools=false ise kullanıcıya kısa, doğrudan bir cevap "
            "yaz (aksi halde boş bırak).\n"
            "Emin değilsen needs_tools=true seç ve plan yap (araçlarla doğrula).",
        ),
        ("placeholder", "{messages}"),
    ]
)

REPLANNER_PROMPT = ChatPromptTemplate.from_template(
    """Verilen hedef için adım adım bir plan yürütüyordun.

Hedefin şuydu:
{input}

Orijinal planın:
{plan}

Şu ana kadar tamamladığın adımlar ve sonuçları:
{past_steps}

Planı buna göre güncelle. Eğer başka bir işlem gerekmiyorsa ve kullanıcıya
cevap verebilecek durumdaysan, o cevabı döndür (Response). Aksi halde planı
doldur — SADECE henüz yapılmamış, hâlâ yapılması gereken adımları ekle.
Zaten tamamlanmış adımları plana tekrar KOYMA."""
)
