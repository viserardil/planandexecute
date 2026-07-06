"""Merkezi konfigürasyon ve LLM fabrikası.

LLM sağlayıcısı buradan bağlanır. Kendi sunucundaki model OpenAI-uyumlu bir
endpoint sunduğu için ChatOpenAI kullanıyoruz; base_url'i .env üzerinden
sunucuna çeviriyoruz. Böylece mimarinin geri kalanı sağlayıcıdan bağımsız kalır.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()


@dataclass(frozen=True)
class Settings:
    base_url: str = os.getenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    api_key: str = os.getenv("OPENAI_API_KEY", "sk-no-key-required")
    model_name: str = os.getenv("MODEL_NAME", "qwen2.5-7b-instruct")
    temperature: float = float(os.getenv("TEMPERATURE", "0.0"))
    max_tokens: int = int(os.getenv("MAX_TOKENS", "1024"))


settings = Settings()


def get_llm(**overrides) -> ChatOpenAI:
    """Konfigüre edilmiş bir sohbet modeli döndürür.

    Planner / executor / replanner aynı fabrikayı kullanır; farklı bir sıcaklık
    ya da model gerekiyorsa ``get_llm(temperature=0.7)`` gibi override geçilebilir.
    """
    params = dict(
        model=settings.model_name,
        base_url=settings.base_url,
        api_key=settings.api_key,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
    )
    params.update(overrides)
    return ChatOpenAI(**params)
