"""Merkezi konfigürasyon ve LLM fabrikası — SAĞLAYICI-BAĞIMSIZ.

Amaç: HANGİ LLM sağlayıcısının anahtarını verirsen ver sistem çalışsın. İki yol:

1) OpenAI-UYUMLU endpoint (VARSAYILAN — ekstra bağımlılık yok). Neredeyse tüm
   sağlayıcılar bunu sunar; sadece üç şeyi ayarla: LLM_API_KEY, LLM_MODEL,
   LLM_BASE_URL. Örnek base_url'ler:
     OpenAI       : https://api.openai.com/v1
     Gemini       : https://generativelanguage.googleapis.com/v1beta/openai/
     Groq         : https://api.groq.com/openai/v1
     Together     : https://api.together.xyz/v1
     OpenRouter   : https://openrouter.ai/api/v1
     DeepInfra    : https://api.deepinfra.com/v1/openai
     HF Router    : https://router.huggingface.co/v1        (varsayılan)
     Ollama/yerel : http://localhost:11434/v1               (api_key='ollama')

2) NATIVE sağlayıcı (LLM_PROVIDER=google_genai | anthropic | groq | ...). Bunun
   için o sağlayıcının LangChain paketini kurman gerekir (ör.
   `uv add langchain-google-genai`). init_chat_model ile kurulur.

Geriye dönük uyum: LLM_* yoksa eski HF_* değişkenleri kullanılır; hiçbir şey
ayarlanmazsa HF Router + Qwen (deepinfra) varsayılanına düşer.

Planner/executor/replanner HEPSİ bu tek fabrikayı kullanır; böylece karşılaştırmada
tek değişken MİMARİ olur. NOT: Bu proje structured output (planner/replanner) ve
tool-calling (executor) kullanır; seçtiğin sağlayıcı/model bunları desteklemeli.
Sağlayıcı json_schema'yı desteklemiyorsa LLM_STRUCTURED_METHOD=function_calling
(veya json_mode) dene.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# Hiçbir env yoksa düşülecek son çare (HF Router; sağlayıcı deepinfra'ya sabit).
DEFAULT_MODEL = "Qwen/Qwen3.5-122B-A10B:deepinfra"
DEFAULT_BASE_URL = "https://router.huggingface.co/v1"


def _first(*names: str) -> str | None:
    """Verilen env adlarından ilk dolu olanı döndürür."""
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


@dataclass(frozen=True)
class Settings:
    # provider: "openai" → OpenAI-uyumlu (base_url sağlayıcıyı seçer). Diğerleri
    # (google_genai, anthropic, groq, ...) init_chat_model ile native kurulur.
    provider: str = field(default_factory=lambda: (os.getenv("LLM_PROVIDER") or "openai").strip())
    model_name: str = field(default_factory=lambda: _first("LLM_MODEL", "HF_MODEL") or DEFAULT_MODEL)
    base_url: str | None = field(default_factory=lambda: _first("LLM_BASE_URL", "HF_BASE_URL") or DEFAULT_BASE_URL)
    api_key: str | None = field(
        default_factory=lambda: _first(
            "LLM_API_KEY", "HF_TOKEN", "HUGGINGFACEHUB_API_TOKEN",
            "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GROQ_API_KEY",
        )
    )
    temperature: float = field(default_factory=lambda: float(os.getenv("TEMPERATURE", "0.1")))
    max_tokens: int = field(default_factory=lambda: int(os.getenv("MAX_TOKENS", "4096")))
    # structured output yöntemi: boş → LangChain varsayılanı (OpenAI/HF'de json_schema).
    structured_method: str | None = field(default_factory=lambda: (os.getenv("LLM_STRUCTURED_METHOD") or "").strip() or None)


settings = Settings()


def structured_output_kwargs() -> dict:
    """with_structured_output için ek argümanlar (yöntem ayarlıysa)."""
    return {"method": settings.structured_method} if settings.structured_method else {}


def get_llm(**overrides):
    """Konfigüre edilmiş bir sohbet modeli döndürür (sağlayıcıdan bağımsız).

    provider="openai" (varsayılan) ise ChatOpenAI + base_url kullanılır — bu tek
    yol OpenAI, Gemini, Groq, Together, OpenRouter, DeepInfra, HF Router ve yerel
    (Ollama/vLLM) sağlayıcılarını kapsar. Aksi halde init_chat_model ile native
    sağlayıcı kurulur (paketi kurulu olmalı).
    """
    if not settings.api_key:
        raise RuntimeError(
            "LLM API anahtarı bulunamadı. .env'e LLM_API_KEY=<anahtar> ekle "
            "(HF için HF_TOKEN da olur)."
        )

    params = dict(
        model=settings.model_name,
        api_key=settings.api_key,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
    )

    if settings.provider == "openai":
        from langchain_openai import ChatOpenAI

        if settings.base_url:
            params["base_url"] = settings.base_url
        params.update(overrides)
        return ChatOpenAI(**params)

    # Native sağlayıcı (google_genai, anthropic, groq, mistralai, ollama, ...).
    try:
        from langchain.chat_models import init_chat_model
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Native sağlayıcı için 'langchain' paketi gerekli: uv add langchain"
        ) from exc

    params.update(overrides)
    model = params.pop("model")
    try:
        return init_chat_model(model, model_provider=settings.provider, **params)
    except ImportError as exc:
        raise RuntimeError(
            f"'{settings.provider}' sağlayıcısının LangChain entegrasyonu kurulu değil. "
            f"Örn: uv add langchain-{settings.provider.replace('_', '-')}"
        ) from exc
