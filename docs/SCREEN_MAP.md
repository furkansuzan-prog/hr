# HRSWI — Ekran Haritası · Agent 01 / 07 (güncelleme: A07, 2026-11-05)

| Menü | Rota | Gerekli izin | Durum | İçerik |
|---|---|---|---|---|
| Genel Bakış | `/` | kpi.read | ✔ | 11–12 KPI kutusu, personel/devir/fazla mesai-devamsızlık trendleri, 3 aylık tahmin (%95 bant), firma bazında personel, kritik riskler, öncelikli aksiyonlar |
| Organizasyon | `/contractors?view=org` | org.read | ✔ (salt okunur) | Tarihe göre geçerli yüklenici hiyerarşisi, kademe, paylaşım izni |
| Personel Analitiği | `/people` | people.read (+ people.write: satır işlemleri) | ✔ | İşe giriş, devir, devamlılık, 90 gün ayrılış, devamsızlık; sayfalı personel listesi (PII maskeleme); bitiş tarihi olmayan satırlarda **Sonlandır** (tarih + ayrılış kategorisi) ve **Transfer** (hedef firma + tarih) diyalogları — record_version kişi geçmişinden alınır, 409 çakışmada güncel sürüm yeniden yüklenir |
| Sürdürülebilirlik | `/sustainability` | sustainability.read | ✔ | KPI-015 gösterge ailesi, anket toplamları (n≥10), standart bazlı veri kapsamı |
| Verimlilik | `/productivity` | kpi.read | ✔ | KPI-004/005/013/006/007/010; verimlilik ↔ fazla mesai gerilimi grafiği |
| İş Gücü Planlama | `/planning` | forecast.read | ✔ | KPI-006 kutusu + planlanan/gerçekleşen ortalama ve sapma; `breakdown.monthly`'den aylık planlanan↔gerçekleşen grafiği; personel tahmini (ufuk 3/6/12 ay), GERÇEKLEŞEN/TAHMİN etiketleri, %80/%95 aralık bandı, tahmin tablosu, model + geri test MAPE. (Senaryo simülasyonu hâlâ planlı) |
| Alt Yükleniciler | `/contractors` | kpi.read | ✔ | 7 KPI ile firma karşılaştırma matrisi + hiyerarşi |
| İş Sağlığı ve Güvenliği | `/hse` | hse.read | ✔ | KPI-014A LTIFR ve KPI-014B TRIR kutuları, KPI başına ayrı aylık grafik (farklı çarpanlar: 1M / 200k saat — toplanamaz/karşılaştırılamaz açıklaması), firma bazında tablo; meslek grubu filtresinin güvenlik oranlarında desteklenmediği notu |
| Eğitim ve Yetkinlik | `/training` | training.read | ✔ | KPI-008 (eğitim saati/çalışan, yıllık) ve KPI-009 (yetkinlik kapsama) kutuları, aylık seriler, firma bazında tablo |
| Maliyet Yönetimi | `/cost` | cost.read | ✔ | KPI-019 ve KPI-010 kutuları, aylık seriler (hiç hesaplanamayan seri için açık mesaj), firma bazında tablo; `insufficient_data` notları sınıflandırılarak gösterilir (Eksik döviz kuru / Karışık birimler / Eksik kaynak verisi) — değer asla 0 gösterilmez |
| Yapay Zekâ Analiz Merkezi | `/assistant` | assistant.use | ✔ | Doğal dil soru, kanıt (araçlar + veri), tahmin/gerçekleşen etiketi, uyarılar |
| Risk ve Aksiyonlar | `/risks` | risk.read | ✔ (salt okunur) | Risk kaydı, aksiyon başlangıç/hedef/sonuç/etkinlik |
| Raporlama | `/reports` | report.read (+export) | ✔ | 2 hazır rapor × XLSX/PDF/CSV; 10 rapor planlandı |
| Veri Entegrasyonları | `/imports` | import.write (+ people.write: kimlik kuyruğu) | ✔ | Yükle → eşleştir → doğrula → commit; hata raporu; kaynak durumu ve senkronizasyon; **Kimlik eşleştirme kuyruğu** (açık incelemeler: Birleştir / Yeni kişi / Reddet, onay diyaloğu) |
| KPI Kataloğu | `/kpis`, `/kpis/[code]` | kpi.read | ✔ | 52 tanım; detayda değer, pay/payda, kalite, formül versiyonları, köken, aylık seri, firma kırılımı |
| Sistem Yönetimi | `/admin` | admin.users | ✔ | Kullanıcı tablosu (rol çipleri: rol · kapsam · geçerlilik; geri alınmış atamalar üstü çizili), arama, kullanıcı oluşturma (başlangıç parolası bir kez gösterilir + kopyala + uyarı), rol atama (rol + kapsam: tek firma / tüm kiracı), onaylı geri alma, rol kataloğu |
| Giriş | `/login` | — | ✔ | |

Ortak: tüm analitik ekranlarda aynı filtre çubuğu (dönem, firma, proje; tarayıcıda saklanır), TR/EN/RU, açık/koyu tema, mobil menü, "SENTETİK DEMO VERİSİ" bandı.

Yetkisiz kullanıcı: menü öğesi gizlenir; doğrudan URL'de "Bu ekrana erişim yetkiniz yok" kartı ve gerekli izin gösterilir (API aynı kuralı ayrıca uygular).

Üst çubuk: dil, tema, **Tüm oturumlardan çık** (`POST /api/v1/auth/logout-all`, onaylı), çıkış.

Ortak bileşenler (A07): `components/ui/dialog.tsx` (yerel `<dialog>` + showModal: arka plan etkisiz, Esc kapatır, Tab tuzağı, odak geri döner, aria-labelledby/-describedby; `ConfirmDialog`, `Field`), `components/kpi-views.tsx` (`PermGate`, `TileGrid`, `NotesPanel`, `SeriesCard`, `ByEntityTable`, `InfoNote`).
