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
