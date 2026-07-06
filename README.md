# Plan-and-Execute vs ReAct — Performans Analizi

İki popüler LLM ajan mimarisini — **Plan-and-Execute** ve **ReAct** — aynı görevler
üzerinde karşılaştıran bir araştırma/staj projesi. Amaç, iki yaklaşımı adil koşullarda
(aynı model, aynı araçlar, aynı görevler) çalıştırıp **doğruluk**, **LLM çağrı sayısı**,
**token tüketimi**, **süre** ve **adım sayısı** gibi metrikler üzerinden kıyaslamak.

> **Durum:** Bu aşamada **Plan-and-Execute** mimarisinin iskeleti tamamlandı.
> ReAct tarafı, ortak benchmark seti ve metrik toplama katmanı sıradaki adımlarda eklenecek.

---

## İçindekiler

- [Motivasyon](#motivasyon)
- [Mimariler](#mimariler)
  - [Plan-and-Execute](#plan-and-execute)
  - [ReAct (karşılaştırma tarafı)](#react-karşılaştırma-tarafı)
- [Dizin yapısı](#dizin-yapısı)
- [Gereksinimler](#gereksinimler)
- [Kurulum (uv)](#kurulum-uv)
- [Ortam değişkenleri (.env)](#ortam-değişkenleri-env)
- [Çalıştırma](#çalıştırma)
- [Nasıl çalışır (veri akışı)](#nasıl-çalışır-veri-akışı)
- [Yapılandırma noktaları](#yapılandırma-noktaları)
- [Sürüm kontrolü / gizli dosyalar](#sürüm-kontrolü--gizli-dosyalar)
- [Sıradaki adımlar](#sıradaki-adımlar)
- [Kaynaklar](#kaynaklar)

---

## Motivasyon

**ReAct** ("Reason + Act"), ajanı her adımda *düşün → araç kullan → gözlemle* döngüsüne
sokar. Esnektir ama uzun ve çok adımlı görevlerde döngü sayısı arttıkça hem maliyet
(LLM çağrısı/token) hem de dağılma/sapma riski büyür.

**Plan-and-Execute**, önce tüm görevi bir **plana** böler, sonra adımları sırayla
yürütür ve gerektiğinde **yeniden planlar (replan)**. Bu, uzun görevlerde genelde daha
az yüksek-seviye LLM çağrısı ve daha tutarlı ilerleme sağlar.

Bu proje "hangisi ne zaman daha iyi?" sorusunu **ölçülebilir** hale getirmeyi hedefler.

---

## Mimariler

### Plan-and-Execute

```
START → planner → executor → replanner ──▶ END
                     ▲            │
                     └────────────┘
                (plan bitmediyse tekrar yürüt)
```

| Düğüm | Görevi |
|-------|--------|
| **planner** | Görev için baştan sona bir adım listesi üretir (tek yüksek-seviye LLM çağrısı). |
| **executor** | Plandaki **ilk** adımı bir ReAct alt-ajanıyla yürütür; araçları burada kullanır. |
| **replanner** | Tamamlanan adımlara ve sonuçlara bakar; ya planı günceller ya da nihai cevabı döndürür. |

Karar mantığı: `replanner` bir `Response` üretirse graf **biter**; bir `Plan` üretirse
kalan adımlar için `executor`'a geri dönülür.

### ReAct (karşılaştırma tarafı)

Henüz eklenmedi. Eklenince aynı LLM ve **aynı araç setini** (`tools.py`) kullanan bir
`create_react_agent` grafiği olacak; böylece tek değişken mimari olacak.

---

## Dizin yapısı

```
Staj_plan_execute/
├── pyproject.toml            # proje tanımı & bağımlılıklar (uv ile yönetilir)
├── .env.example             # ortam değişkeni şablonu (bunu kopyalayıp .env yap)
├── .gitignore               # push edilmeyecek dosyalar
├── README.md
└── src/
    └── plan_execute/
        ├── __init__.py       # paket girişi (build_plan_execute_graph)
        ├── config.py         # LLM fabrikası — kendi sunucundaki OpenAI-uyumlu endpoint
        ├── schemas.py        # Plan / Response / Act (structured output şemaları)
        ├── state.py          # PlanExecute — graf boyunca taşınan durum
        ├── prompts.py        # planner & replanner prompt şablonları
        ├── tools.py          # ortak araçlar (web arama + hesap makinesi)
        ├── nodes.py          # plan_step / execute_step / replan_step düğümleri
        ├── graph.py          # StateGraph kurulumu ve derlenmesi
        └── run.py            # komut satırı girişi (CLI)
```

---

## Gereksinimler

- **Python** ≥ 3.10
- **uv** (paket & ortam yöneticisi) — [kurulum](https://docs.astral.sh/uv/getting-started/installation/)
- Erişilebilir bir **OpenAI-uyumlu LLM endpoint'i** (kendi sunucunda vLLM / Ollama /
  LM Studio / TGI vb.)

---

## Kurulum (uv)

> Bu proje **pip değil, uv** ile yönetilir.

```powershell
# 1) Sanal ortam oluştur
uv venv

# 2) Ortamı etkinleştir (Windows PowerShell)
.venv\Scripts\activate

# 3) Projeyi ve bağımlılıklarını (editable) kur
uv pip install -e .
```

> Alternatif: `uv sync` ile `pyproject.toml` + `uv.lock`'tan birebir kurulum yapabilirsin.

---

## Ortam değişkenleri (.env)

`.env.example` dosyasını kopyalayıp kendi sunucuna göre doldur:

```powershell
Copy-Item .env.example .env
```

| Değişken | Açıklama | Örnek |
|----------|----------|-------|
| `OPENAI_BASE_URL` | Kendi sunucundaki OpenAI-uyumlu endpoint | `http://192.168.1.50:8000/v1` |
| `OPENAI_API_KEY` | Sunucun key istemiyorsa placeholder yeterli (boş bırakma) | `sk-no-key-required` |
| `MODEL_NAME` | Sunucunda yüklü model adı | `qwen2.5-7b-instruct` |
| `TEMPERATURE` | Örnekleme sıcaklığı (deterministik için 0.0) | `0.0` |
| `MAX_TOKENS` | Yanıt başına maksimum token | `1024` |

Yaygın endpoint yolları: vLLM `:8000/v1`, Ollama `:11434/v1`, LM Studio `:1234/v1`.

> ⚠️ `.env` **asla commit edilmez** — `.gitignore` bunu engeller. Paylaşılacak olan sadece
> `.env.example` şablonudur.

---

## Çalıştırma

```powershell
# Varsayılan örnek görevle
uv run python -m plan_execute.run

# Kendi görevinle
uv run python -m plan_execute.run "Türkiye'nin yüzölçümünün karekökü kaçtır?"
```

Çıktı, her düğümün (planner / executor / replanner) ürettiği güncellemeleri canlı
akış (streaming) halinde yazdırır ve sonda nihai cevabı gösterir.

---

## Nasıl çalışır (veri akışı)

Graf, `PlanExecute` adlı ortak bir **durum** (`state.py`) üzerinde çalışır:

```python
class PlanExecute(TypedDict):
    input: str                                        # kullanıcının görevi
    plan: List[str]                                   # henüz yürütülmemiş adımlar
    past_steps: Annotated[List[Tuple[str, str]], add] # (adım, sonuç) — birikerek eklenir
    response: str                                     # doluysa graf sonlanır
```

1. **planner** → `input`'tan bir `plan` üretir.
2. **executor** → `plan[0]`'ı yürütür, sonucu `past_steps`'e ekler.
3. **replanner** → `Response` verirse `response` dolar (graf biter); `Plan` verirse
   `plan` güncellenir ve tekrar **executor**'a dönülür.

Structured output (`schemas.py`) sayesinde planner her zaman geçerli bir `Plan`,
replanner ise geçerli bir `Act` (`Plan` **veya** `Response`) döndürür.

---

## Yapılandırma noktaları

- **Model / sağlayıcı:** `config.py` → `get_llm()`. Tek nokta; sağlayıcı değişse bile
  mimarinin geri kalanı bundan bağımsız.
- **Araçlar:** `tools.py` → `get_tools()`. Plan-Execute ve ReAct **aynı** listeyi
  kullanır (adil karşılaştırma). Yeni araç eklemek için buraya ekle.
- **Prompt'lar:** `prompts.py`. Planlama/yeniden planlama davranışını buradan ayarla.
- **Özyineleme limiti:** `run.py` → `config={"recursion_limit": 50}`. Sonsuz döngüye
  karşı güvenlik sınırı.

---

## Sürüm kontrolü / gizli dosyalar

`.gitignore` şunları **kasıtlı olarak** repodan dışlar:

- **Sırlar:** `.env`, `*.key`, `*.pem`, `credentials.json` — API key & endpoint sızmasın.
- **Ortamlar:** `.venv/`, `venv/`, `.python-version`.
- **Cache/derleme:** `__pycache__/`, `*.egg-info/`, `build/`, `dist/`, `.ruff_cache/`, `.mypy_cache/`, `.pytest_cache/`.
- **LLM artefaktları:** LangGraph checkpoint'leri (`*.sqlite`, `.langgraph/`), vektör DB (`.chroma/`).
- **Deney çıktıları:** `results/`, `logs/`, `benchmark_results/` — büyük ve yeniden üretilebilir.
- **IDE & OS dosyaları:** `.vscode/`, `.idea/`, `Thumbs.db`, `.DS_Store`.

Paylaşılan tek şablon `.env.example`'dır (istisna olarak takip edilir).

> Not: `uv.lock` genelde **commit edilir** (tekrarlanabilir kurulum için). Projeyi bir
> kütüphane olarak yayımlamayacaksan lock dosyasını repoda tut.

---

## Sıradaki adımlar

- [ ] **ReAct modülü:** aynı LLM + `tools.py` ile `create_react_agent` grafiği
- [ ] **Benchmark seti:** ortak, çok adımlı görevler (araç kullanan) + beklenen cevaplar
- [ ] **Metrik toplama:** LLM çağrı sayısı, token, süre, adım sayısı (callback/tracing)
- [ ] **Değerlendirme:** doğruluk skoru + karşılaştırma raporu / grafikler
- [ ] (Opsiyonel) LangSmith veya yerel trace ile adım adım izleme

---

## Kaynaklar

- Wang et al., *Plan-and-Solve Prompting* (2023)
- Yao et al., *ReAct: Synergizing Reasoning and Acting in Language Models* (2022)
- [LangGraph — Plan-and-Execute rehberi](https://langchain-ai.github.io/langgraph/tutorials/plan-and-execute/plan-and-execute/)
