"""Merkezi konfigürasyon ve LLM fabrikası.

ReAct tarafıyla ADİL karşılaştırma için aynı model/sağlayıcı kullanılır:
model, HuggingFace Router üzerinden OpenAI-uyumlu bir chat completions API ile
sunulur. Değerler .env'den (HF_MODEL / HF_BASE_URL / HF_TOKEN) okunur; aşağıdaki
DEFAULT_* değerleri yalnızca env boşsa devreye giren yedeklerdir.

Planner / executor / replanner hepsi bu tek fabrikayı kullanır; böylece
karşılaştırmada tek değişken MİMARİ olur, model/sağlayıcı değil.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()

# ReAct projesindeki src/react_agent.py ile birebir aynı yedekler.
DEFAULT_MODEL = "Qwen/Qwen3.5-122B-A10B"
DEFAULT_BASE_URL = "https://router.huggingface.co/v1"


def _get_token() -> str | None:
    """HF Router token'ı: HF_TOKEN ya da HUGGINGFACEHUB_API_TOKEN."""
    return os.getenv("HF_TOKEN") or os.getenv("HUGGINGFACEHUB_API_TOKEN")


@dataclass(frozen=True)
class Settings:
    model_name: str = field(default_factory=lambda: os.getenv("HF_MODEL") or DEFAULT_MODEL)
    base_url: str = field(default_factory=lambda: os.getenv("HF_BASE_URL") or DEFAULT_BASE_URL)
    api_key: str | None = field(default_factory=_get_token)
    temperature: float = field(default_factory=lambda: float(os.getenv("TEMPERATURE", "0.1")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("MAX_TOKENS", "1024")))


settings = Settings()


def get_llm(**overrides) -> ChatOpenAI:
    """Konfigüre edilmiş bir sohbet modeli döndürür.

    Planner / executor / replanner aynı fabrikayı kullanır; farklı bir sıcaklık
    ya da model gerekiyorsa ``get_llm(temperature=0.0)`` gibi override geçilebilir.
    """
    token = settings.api_key
    if not token:
        raise RuntimeError(
            "HuggingFace API token bulunamadı. .env dosyasına HF_TOKEN=<anahtar> ekle."
        )
    params = dict(
        model=settings.model_name,
        base_url=settings.base_url,
        api_key=token,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
    )
    params.update(overrides)
    return ChatOpenAI(**params)
