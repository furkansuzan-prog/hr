# HRSWI — Ekran Haritası · Agent 01 / 07

| Menü | Rota | Gerekli izin | Durum | İçerik |
|---|---|---|---|---|
| Genel Bakış | `/` | kpi.read | ✔ | 11–12 KPI kutusu, personel/devir/fazla mesai-devamsızlık trendleri, 3 aylık tahmin (%95 bant), firma bazında personel, kritik riskler, öncelikli aksiyonlar |
| Organizasyon | `/contractors?view=org` | org.read | ✔ (salt okunur) | Tarihe göre geçerli yüklenici hiyerarşisi, kademe, paylaşım izni |
| Personel Analitiği | `/people` | people.read | ✔ | İşe giriş, devir, devamlılık, 90 gün ayrılış, devamsızlık; sayfalı personel listesi (PII maskeleme) |
| Sürdürülebilirlik | `/sustainability` | sustainability.read | ✔ | KPI-015 gösterge ailesi, anket toplamları (n≥10), standart bazlı veri kapsamı |
| Verimlilik | `/productivity` | kpi.read | ✔ | KPI-004/005/013/006/007/010; verimlilik ↔ fazla mesai gerilimi grafiği |
| İş Gücü Planlama | — | — | Planlandı (FAZ 5) | Senaryo simülasyonu, iş paketi bazlı ihtiyaç |
| Alt Yükleniciler | `/contractors` | kpi.read | ✔ | 7 KPI ile firma karşılaştırma matrisi + hiyerarşi |
| İSG | — | — | Planlandı (FAZ 4) | (KPI-014A/B şu an dashboard ve KPI detayında) |
| Eğitim ve Yetkinlik | — | — | Planlandı (FAZ 4) | (KPI-008/009 şu an dashboard'da) |
| Maliyet Yönetimi | — | — | Planlandı (FAZ 5) | (KPI-010/019 cost.read ile) |
| Yapay Zekâ Analiz Merkezi | `/assistant` | assistant.use | ✔ | Doğal dil soru, kanıt (araçlar + veri), tahmin/gerçekleşen etiketi, uyarılar |
| Risk ve Aksiyonlar | `/risks` | risk.read | ✔ (salt okunur) | Risk kaydı, aksiyon başlangıç/hedef/sonuç/etkinlik |
| Raporlama | `/reports` | report.read (+export) | ✔ | 2 hazır rapor × XLSX/PDF/CSV; 10 rapor planlandı |
| Veri Entegrasyonları | `/imports` | import.write | ✔ | Yükle → eşleştir → doğrula → commit; hata raporu; kaynak durumu ve senkronizasyon |
| KPI Kataloğu | `/kpis`, `/kpis/[code]` | kpi.read | ✔ | 52 tanım; detayda değer, pay/payda, kalite, formül versiyonları, köken, aylık seri, firma kırılımı |
| Sistem Yönetimi | — | — | Planlandı (FAZ 7) | Kullanıcı/rol atama UI (API modeli hazır) |
| Giriş | `/login` | — | ✔ | |

Ortak: tüm analitik ekranlarda aynı filtre çubuğu (dönem, firma, proje; tarayıcıda saklanır), TR/EN/RU, açık/koyu tema, mobil menü, "SENTETİK DEMO VERİSİ" bandı.
