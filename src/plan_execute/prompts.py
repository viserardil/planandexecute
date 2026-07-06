"""Planner ve replanner için prompt şablonları."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

PLANNER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "Verilen hedef için basit, adım adım bir plan çıkar.\n"
            "Bu plan, doğru şekilde yürütülürse hedefe ulaştıracak ayrı görevlerden "
            "oluşmalı. Gereksiz adım ekleme. Son adımın sonucu nihai cevap olmalı.\n"
            "Her adım, yürütülmesi için gereken tüm bilgiyi içermeli — adımları atlama.",
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
