"""Planner ve replanner için prompt şablonları."""

from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate

from plan_execute.tools import TOOLS

# Planner araç ADLARINI görmeli: aksi halde "grafiği oluştur" gibi muğlak adım yazar,
# executor da onu araç çağrısına çeviremez. Yalnızca adlar veriliyor (~60 token) —
# tam açıklamalar hem pahalı olurdu hem de içlerindeki JSON örnekleri ({...})
# ChatPromptTemplate'in değişken ayrıştırıcısını kırardı.
_TOOL_NAMES = ", ".join(TOOLS)

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
            "Emin değilsen needs_tools=true seç ve plan yap (araçlarla doğrula).\n\n"
            f"Yürütücünün kullanabileceği araçlar: {_TOOL_NAMES}.\n"
            "Her adımı bu araçlarla yapılabilecek somut bir iş olarak yaz ve kullanılacak "
            "aracın ADINI adımda belirt.\n"
            "GRAFİK KURALI: Kullanıcı grafik/çizim/görselleştirme istiyorsa plana MUTLAKA bir "
            "çizim adımı koy — hisse fiyat/gelir grafiği için plot_chart, elindeki sayısal "
            "veriyi çizmek için visualize_data. Grafik ASLA metinle, ASCII'yle veya Python "
            "koduyla anlatılmaz; ilgili araç çalıştırılır ve PNG üretilir.",
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
Zaten tamamlanmış adımları plana tekrar KOYMA.

GRAFİK KURALI: Hedef bir grafik/görselleştirme içeriyorsa ve yukarıdaki sonuçlarda
plot_chart ya da visualize_data'nın döndürdüğü bir .png dosya yolu HENÜZ yoksa,
görev BİTMEMİŞTİR — Response DÖNME, çizim adımını plana koy (hisse fiyat/gelir
grafiği: plot_chart, elindeki sayısal veri: visualize_data). Cevabında asla
"(grafik burada gösterilecektir)" gibi yer tutucu, ASCII grafik veya matplotlib
kodu yazma; grafik üretildiyse aracın döndürdüğü dosya yolunu cevabında aynen ver."""
)
