# Plan-and-Execute vs ReAct — Performans Analizi

İki LLM ajan mimarisini — **Plan-and-Execute** ve **ReAct** — **aynı görevler**,
**aynı model**, **aynı araçlar** ve **aynı metrikler** üzerinde karşılaştıran bir
staj projesi. Amaç: iki yaklaşımı adil koşullarda çalıştırıp **başarı**,
**LLM çağrı sayısı**, **token tüketimi**, **süre**, **araç çağrısı** ve
**adım sayısı** üzerinden kıyaslamak.

> **Durum:** Plan-and-Execute mimarisi ReAct tarafıyla **hizalandı**: aynı 4 araç,
> aynı model/sağlayıcı (HF Router), aynı 10 KKB görevi ve aynı metrik/çıktı şeması.
> İki projenin sonuç JSON'ları doğrudan karşılaştırılabilir.

Bu depo Plan-Execute tarafıdır. ReAct tarafı komşu `Staj_reAct` projesindedir.

---

## Mimariler

### Plan-and-Execute (bu depo)

```
START → planner → executor → replanner ──▶ END
                     ▲            │
                     └────────────┘
                (plan bitmediyse tekrar yürüt)
```

| Düğüm | Görevi |
|-------|--------|
| **planner** | Görev için baştan sona bir adım listesi üretir (tek yüksek-seviye LLM çağrısı). |
| **executor** | Plandaki **ilk** adımı bir `create_react_agent` alt-ajanıyla yürütür; araçları burada kullanır. |
| **replanner** | Tamamlanan adımlara/sonuçlara bakar; planı günceller veya nihai cevabı döndürür. |

Karar mantığı: `replanner` bir `Response` üretirse graf **biter**; bir `Plan`
üretirse kalan adımlar için `executor`'a geri dönülür.

### ReAct (karşılaştırma tarafı — `Staj_reAct`)

Her adımda *düşün → araç çağır → gözlemle* döngüsü kuran, elle yazılmış bir ReAct
ajanı. **Aynı** modeli ve **aynı** araç setini kullanır; böylece tek değişken
mimaridir.

---

## Adil karşılaştırma için hizalama

| Konu | Ortak değer |
|------|-------------|
| **Model / sağlayıcı** | HF Router (OpenAI-uyumlu), `HF_MODEL` (varsayılan `Qwen/Qwen3.5-122B-A10B`) |
| **Araçlar** | `calculator` (AST-güvenli), `kkb_bilgi` (Ardıldeks formülü), `sql_sorgu` (persona SQLite), `web_arama` (Tavily + disk cache) |
| **Görev seti** | 10 KKB görevi (`test/test_plan_execute_agent.py` içindeki `CASES`) |
| **Metrikler** | success, steps, tool_calls, tools_used, input/output/total_tokens, duration_ms, trace |
| **Web cache** | `scratch/web_cache/` — ReAct'ten kopyalandı; aynı sorgu iki tarafta aynı gözlemi verir |

> `web_cache` paylaşıldığı için mevcut 10 görevin web gözlemleri iki mimaride
> **birebir aynıdır** — böylece tek değişken mimari kalır.

---

## Dizin yapısı

```
Staj_plan_execute/
├── main.py                      # metrik özetli çalıştırıcı (ReAct main.py ile simetrik)
├── pyproject.toml               # bağımlılıklar (uv)
├── .env.example                 # ortam değişkeni şablonu
├── scratch/
│   ├── web_cache/               # Tavily sonuç cache'i (ReAct'ten kopya)
│   └── plan_execute_runs/       # benchmark çıktıları (JSON + JSONL)
├── src/plan_execute/
│   ├── config.py                # LLM fabrikası — HF Router (ReAct ile aynı)
│   ├── schemas.py               # Plan / Response / Act (structured output)
│   ├── state.py                 # PlanExecute — graf durumu
│   ├── prompts.py               # planner & replanner prompt'ları
│   ├── tools.py                 # ortak 4 araç (@tool sarmalı)
│   ├── nodes.py                 # plan_step / execute_step / replan_step
│   ├── graph.py                 # StateGraph kurulumu
│   ├── agent.py                 # PlanExecuteAgent + metrik callback (RunResult)
│   └── run.py                   # canlı stream CLI (adım adım izlemek için)
└── test/
    └── test_plan_execute_agent.py  # 10 görev + benchmark harness (pytest & CLI)
```

---

## Kurulum (uv)

```powershell
# OneDrive altında hardlink desteklenmediği için copy modu gerekir:
$env:UV_LINK_MODE = "copy"
uv sync
Copy-Item .env.example .env      # sonra .env içine HF_TOKEN gir
```

---

## Ortam değişkenleri (.env)

| Değişken | Açıklama |
|----------|----------|
| `HF_TOKEN` | **Zorunlu.** HuggingFace Router API anahtarı. |
| `HF_MODEL` | Model adı (varsayılan `Qwen/Qwen3.5-122B-A10B`). |
| `HF_BASE_URL` | Router endpoint'i (varsayılan `https://router.huggingface.co/v1`). |
| `TAVILY_API_KEY` | `web_arama` için; cache'de olmayan sorgularda gerekir. |
| `TEMPERATURE` / `MAX_TOKENS` | Örnekleme ve yanıt sınırı. |
| `RECURSION_LIMIT` | Graf özyineleme güvenlik sınırı (varsayılan 50). |
| `WEB_CACHE` | `1` cache kullan, `0` her seferinde canlı çağır. |

---

## Çalıştırma

```powershell
$env:UV_LINK_MODE = "copy"

# Örnek görevlerle, metrik özeti basar
uv run python main.py

# Tek bir soru
uv run python main.py "Can Öztürk'ün Ardıldeks skoru kaçtır?"

# Grafiği adım adım izlemek için (metrik yok, sadece akış)
uv run python -m plan_execute.run "Can Öztürk'ün Ardıldeks skoru kaçtır?"

# Tüm benchmark (10 görev) — JSON + JSONL çıktısı üretir
uv run python test/test_plan_execute_agent.py --version pe-v1

# Tek görev
uv run python test/test_plan_execute_agent.py --version pe-tek --case-id 3
```

Benchmark çıktıları `scratch/plan_execute_runs/` altına yazılır:
`test_results_<etiket>.json` (özet + her görev için tam metrik/trace) ve
`test_progress_<etiket>.jsonl` (canlı olay akışı). Bu format ReAct'in
`scratch/react_runs/` çıktısıyla **aynıdır**, yani iki tarafı yan yana koyup
karşılaştırabilirsin.

---

## Toplanan metrikler

| Metrik | Anlamı |
|--------|--------|
| `success` | Nihai cevaba ulaşıldı mı |
| `steps` / `llm_calls` | Toplam LLM çağrısı (planner + executor turları + replan). ReAct'in `steps`'iyle aynı tanım: bir LLM invoke. |
| `plan_steps` | (Plan-Execute'a özgü) Yürütülen plan adımı sayısı |
| `replan_count` | (Plan-Execute'a özgü) Replanner çağrı sayısı |
| `tool_calls` / `tools_used` | Araç çağrı sayısı ve hangi araçların kullanıldığı |
| `input/output/total_tokens` | Token tüketimi (maliyet göstergesi) |
| `duration_ms` | Görev süresi |
| `trace` | Yürütülen (adım, sonuç) çiftleri |

> Metrikler grafiğe eklenen bir LangChain callback ile **tüm** grafiği
> (executor'ın alt-ajanı dâhil) kapsayacak şekilde toplanır.

---

## Sıradaki adımlar

- [ ] İki tarafın JSON'larını okuyup karşılaştırma tablosu/grafik üreten rapor betiği
- [ ] Aynı `--version` etiketiyle her iki projeyi çalıştırıp sonuçları eşleştirme
- [ ] Doğruluk (accuracy) skoru için beklenen cevap anahtarı

---

## Kaynaklar

- Wang et al., *Plan-and-Solve Prompting* (2023)
- Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models* (2022)
- [LangGraph — Plan-and-Execute rehberi](https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/plan-and-execute/)
