# Plan-and-Execute vs ReAct — Genel Gözlemler

**Deney:** Aynı model (OpenAI), aynı 18 araç (`tools.py` iki projede birebir aynı), aynı 55
soru (HF `equity-research-agentic-eval`), aynı çıktı şeması (`test/a.json` v2.0.0).
**Tek değişken: mimari.**

> **Ana bulgu (iki yönlü):**
> - **Basit / az adımlı görevlerde** ReAct aynı işi **~yarı maliyetle** yapıyor — Plan-Execute'un
>   planlama katmanı burada saf ek yük.
> - **Gerçekten çok-adımlı görevlerde** (Plan-Execute'un baştan **3+ adım** planladığı sorular)
>   Plan-Execute **belirgin biçimde daha kapsamlı** sonuç üretiyor: %73 daha fazla veri topluyor.
>
> Yani "biri diğerini ezer" değil — **hangisinin kazandığı görev tipine bağlı.**

---

## 1) Genel karşılaştırma

![Genel karşılaştırma](01_genel_kiyas.png)

| Metrik | Plan-Execute | ReAct | Fark |
|--------|-------------:|------:|-----:|
| "Başarı" | %100 (48/48) | %98 (47/48) | ~eşit |
| Ort. token | 10.273 | 5.088 | ReAct **−50 %** |
| Ort. LLM çağrısı | 8.1 | 3.2 | ReAct **−61 %** |
| Ort. süre | 56.7 sn | 27.0 sn | ReAct **−52 %** |

> ⚠️ **"Başarı" bir kalite ölçüsü DEĞİL.** Bu metrik yalnızca *"ajan bir cevap döndürdü mü"*
> demek — modelin kendi beyanı. Cevabın **doğru** olduğunu göstermez. Gerçek kalite için
> bkz. bölüm 4 ve `test/score.py` (AQS).

> **Not:** Ham koşuda başarısızlıkların çoğu **OpenAI rate-limit (429)** kaynaklıydı (org'un
> 30.000 TPM limiti), mimari değil. Adil kıyas için iki tarafta da 429 alan **7 vaka çıkarıldı**
> (2.8, 5.3–5.7, E.6) → tüm sayılar kalan **N=48** ortak vaka üzerinden. ReAct'in tek kalan
> hatası (E.3) `visualize_data` aracındaki bir bug'dı — **düzeltildi**.

---

## 2) Kademeye göre

![Başarı](02_basari_kademeye_gore.png)

429 gürültüsü çıkınca "başarı" iki tarafta da neredeyse tam. Daha önce görülen
"Plan-Execute T5'te çöküyor" tablosu **gerçek değildi** — o vakalar kota duvarına çarpmıştı.

![Token](03_token_kademeye_gore.png)

![LLM çağrısı](04_llm_cagrisi_kademeye_gore.png)

Maliyet farkı her kademede tutarlı: Plan-Execute ~2 kat token ve çağrı harcıyor
(planner + her adımdan sonra çalışan replanner).

---

## 3) Çok-adımlı görevlerde Plan-Execute daha kapsamlı ⭐

Asıl ayrım burada. Vakaları **Plan-Execute'un baştan kaç adım planladığına** göre ayırdık:

| | **Çok-adımlı** (plan ≥ 3 adım, n=17) | **Basit** (plan ≤ 2 adım, n=30) |
|---|---:|---:|
| Araç çağrısı — PE | **5.18** | 2.20 |
| Araç çağrısı — ReAct | 3.00 | 1.83 |
| **PE'nin veri toplama fazlası** | **+73 %** | +20 % |
| Cevap uzunluğu (PE / ReAct) | 889 / 407 kar. | 666 / 330 kar. |
| PE cevabı daha uzun olan vaka | **17/17 (%100)** | 27/30 |

**Okunuşu:** Plan-Execute her yerde ~2 kat uzun yazıyor — bu tek başına sadece *ayrıntıcılık*.
Asıl kanıt **veri toplamada**: çok-adımlı görevlerde ReAct'ten **%73 daha fazla araç çağırıyor**
(basit görevlerde bu fark sadece %20). Yani baştan çıkarılan plan, ajanı **daha fazla kaynağa
bakmaya** zorluyor ve sonuç daha bütünlüklü oluyor. ReAct ise erken "yeterli" deyip bitirebiliyor.

> Kısacası: **planlamanın karşılığı, gerçekten çok-adımlı görevlerde alınıyor.** Basit
> sorularda ise aynı yapı saf maliyet.

---

## 4) Kritik ders: "cevap üretti" ≠ "doğru cevap"

Cevapları tek tek okuyunca Plan-Execute'un bir zaafı da çıktı — **triyaj fazla ateşliyor**:
olgusal/görselleştirme görevlerinde bile "araç gerekmez" deyip doğrudan cevaplıyor.

| Vaka | Plan-Execute | ReAct |
|------|--------------|-------|
| **1.11** "bar grafik çiz" | ❌ araç çağırmadı → grafik yerine **Python kodu** verdi | ✅ gerçek PNG |
| **3.3** "WMT gelir grafiği" | ❌ `visualize_data` atladı → **ASCII/metin** grafik | ✅ gerçek PNG |
| **3.4** "THY neden düştü?" | ❌ haber/web aramadı → **uydurma** ("...olabilir") | ✅ gerçek haberlere dayalı |
| **1.2** "Aselsan ne iş yapar" | ⚠️ araç yok → **modelin ezberinden** (doğrulanmamış) | ✅ `get_company_info` verisi |

Bunların hepsi `success=True` sayıldı — metriğin neden yanıltıcı olduğunun kanıtı.

---

## 5) Araç kullanımı

![Araç kullanımı](05_arac_kullanimi.png)

Plan-Execute neredeyse her aracı daha çok çağırıyor. Bu hem maliyetin kaynağı **hem de**
çok-adımlı görevlerdeki kapsamlılık avantajının açıklaması (bölüm 3).

---

## 6) Öneri: triyaj yönlendirmeli hibrit

![Hibrit mimari](06_hibrit_mimari_onerisi.png)

Bulgular doğrudan bu tasarıma işaret ediyor:

| Görev tipi | Rota | Gerekçe (veriden) |
|-----------|------|-------------------|
| Kapsam-dışı / selam / muğlak | **Doğrudan cevap** | Araç gereksiz, tek çağrı |
| Basit / 1–2 araç | **ReAct** | Aynı iş, ~yarı maliyet |
| **Çok-adımlı / rapor** | **Plan-Execute** | **+%73 daha fazla veri → daha kapsamlı** |

**Kritik kural:** doğrudan-cevap rotası SADECE gerçekten araç gerektirmeyen sorulara açılmalı —
Plan-Execute'un düştüğü tuzak (bölüm 4) tam da buydu. Emin değilse → ReAct.

---

## 7) Sınırlar ve açık işler

- **Tek koşu.** Kesinlik için tekrarlı koşuların ortalaması gerekir.
- **"Kapsamlılık" ölçüsü dolaylı:** araç sayısı + cevap uzunluğu üzerinden. Uzunluk tek başına
  kalite değildir; asıl sinyal araç/veri kapsamasıdır. Kesin yargı için AQS gerekir.
- **Süre** sağlayıcı gecikmesine duyarlı; token ve LLM çağrısı mimariye bağlı olduğundan daha
  güvenilir — ikisi de aynı yönü işaret ediyor.
- **Doğruluk henüz otomatik ölçülmüyor.** `test/score.py` (AQS — Agent Kalite Skoru) yazıldı:
  dataset'in altın-anahtarlarıyla 6 boyutu (içerik, araç seçimi, gerçeğe dayanma, görev
  tamamlama, halüsinasyon, dil) 0–100 skora çevirir. **Koşulması bekliyor.**
- **Model bağımlılığı:** Aynı deney Qwen ile yapıldığında tablo benzerdi (ikisi de ~%98 "başarı",
  ReAct ~yarı maliyet) — bulgu tek modele özgü değil.
