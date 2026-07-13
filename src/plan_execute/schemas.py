"""Planner ve replanner'ın structured output şemaları."""

from __future__ import annotations

from typing import List, Union

from pydantic import BaseModel, Field


class Plan(BaseModel):
    """Görevi çözmek için sıralı adımlar planı."""

    steps: List[str] = Field(
        description="Takip edilecek, doğru sırada dizilmiş adımlar."
    )


class Response(BaseModel):
    """Kullanıcıya verilecek nihai cevap."""

    response: str = Field(description="Kullanıcıya dönülecek nihai yanıt.")


class Act(BaseModel):
    """Replanner'ın kararı: ya devam etmek için yeni plan, ya da bitirmek için cevap."""

    action: Union[Response, Plan] = Field(
        description=(
            "Yapılacak eylem. Kullanıcıya cevap verilecekse Response kullan. "
            "Hedefe ulaşmak için daha fazla araç kullanımı gerekiyorsa Plan kullan."
        )
    )


class PlannerDecision(BaseModel):
    """Planner'ın triyaj kararı: görev araç gerektiriyorsa plan adımları, aksi
    halde (kapsam dışı/selamlama/netleştirme) doğrudan cevap.

    Union yerine AÇIK alanlar kullanılır: model bazen union branch'ini karıştırıp
    yanıt alanına etiket ('PLAN') yazıyordu; bool + ayrı alanlar bunu önler.
    """

    needs_tools: bool = Field(
        description=(
            "Görev güncel/olgusal veri, hesaplama ya da araç gerektiriyor mu? "
            "Fiyat/temel veri/rasyo/gelir tablosu/analist/teknik/haber/web/grafik/"
            "hesaplama → true. Kapsam dışı istek (emir verme, alım-satım, kişisel "
            "yatırım tavsiyesi), selamlaşma ya da netleştirme gereken muğlak soru → false."
        )
    )
    steps: List[str] = Field(
        default_factory=list,
        description="needs_tools=true ise doğru sırada, ayrı ayrı yürütülebilir plan adımları (aksi halde boş).",
    )
    direct_answer: str = Field(
        default="",
        description="needs_tools=false ise kullanıcıya kısa, doğrudan cevap (aksi halde boş).",
    )
