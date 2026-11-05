# HRSWI — Bağımsız Denetim Raporu · Agent 16

**Denetçi:** Kodu yazmamış, ayrı bağlamda çalışan bağımsız inceleme ajanı. Commit `64e76fe` incelendi. Yöntem: kod okuma, kapsamlı kullanıcılarla salt okunur canlı API çağrıları, salt okunur SQL.
**Düzeltmeler:** Geliştirici (ayrı) tarafından yapıldı. Her düzeltme için regresyon testi eklendi (`backend/tests/test_audit_regressions.py`). Denetçi kendi bulgusunu onaylamaz; kapanış doğrulaması bir sonraki denetim turunda yapılmalıdır.
Test paketi düzeltmeler sonrası: **73/73 geçti**. E2E: **12/12**.

| # | Önem | Bulgu (özet) | Durum | Düzeltme / kanıt |
|---|---|---|---|---|
| 1 | Yüksek | `GET /kpi/runs/{id}` kapsam ve `cost.read` kontrolü yapmadan tüm sonuçları döndürüyordu | **Düzeltildi** | Yalnızca tenant geneli okuyucu veya çalıştıran kullanıcı; maliyet KPI'ları ayrıca tenant geneli `cost.read` · `test_f1_*` |
| 2 | Yüksek | Bastırılmış sonuçlarda sayılar `lineage` içinde sızıyordu | **Düzeltildi** | Motorda tek merkezi `enforce_privacy()` (değer, pay, payda, örneklem, kırılım, köken); önbellek şema sürümü `v2` · `test_f2_*` |
| 3 | Yüksek | Devir kırılımı fiilen hiç maskelenmiyordu (`min_sample=1`); İSG oranlarında örneklem eşiği yoktu | **Düzeltildi** | Kategori < 5 maskeli; LTIFR/TRIR kişi sayısı eşiği · `test_turnover_tenant_excludes_internal_transfer` güncellendi |
| 4 | Yüksek | LTIFR/TRIR meslek filtresinde olayları filtrelemeyip saatleri filtreliyordu (6.67 → 49.51) | **Düzeltildi** | Olaylar mesleğe atanamadığı için meslek filtresinde `insufficient_data` + açıklama · `test_f4_*` |
| 5 | Yüksek | KPI-010 farklı birimlerdeki çıktıları topluyordu | **Düzeltildi** | Birden fazla birim → `insufficient_data` ve birim listesi · `test_f5_*` |
| 6 | Yüksek | Kapalı dönem rakamları canlı yeniden hesaplanıyordu | **Düzeltildi** | Kapalı dönemde kalıcı (dondurulmuş) sonuç sunulur; sonradan gelen veri yalnızca `is_stale` işaretler · `test_f6_*`. Not: dönem kapanmadan önce hesap çalıştırılmamışsa canlı değer döner (kapanış öncesi çalıştırma prosedürü RELEASE_PLAN'a eklenmeli) |
| 7 | Yüksek | Global formül versiyonlarını herhangi bir tenant yöneticisi değiştirebiliyordu; parametreler doğrulanmıyordu; değişmezlik yalnızca gelenekseldi | **Kısmen** | `admin.platform` zorunlu, parametre şeması, satır kilidi, DB tetikleyicisi ile değişmezlik (migration `0003`) · `test_f7_*`. **Açık:** tenant'a özel formüller (B-09) |
| 8 | Orta | Ham TC kimlik no staging `raw` ve önizlemede | **Düzeltildi** (denetim sırasında bağımsız olarak da tespit edilmişti) | Saklamadan önce HMAC · `test_raw_national_id_never_persisted_or_echoed` |
| 9 | Orta | Import batch'leri/DQ sorunları tenant geneli görünür; commit kapsamı tekrar kontrol etmiyordu | **Düzeltildi** | Kapsamlı import kullanıcıları yalnızca kendi batch'lerini görür/commit eder · `test_f9_*` |
| 10 | Orta | Yarıda kalan commit sonrası önbellek/bayatlık geçersizleştirmesi kaybolabiliyordu | **Düzeltildi** | Geçersizleştirme tüm commit edilmiş satırlardan, hata yolunda da · *Ayrı regresyon testi yok* |
| 11 | Orta | Kimlik eşleştirme: yeniden kullanılan dış kimlik sessiz birleştirme; inceleme satırları "committed" sayılıyordu; ham DB hatası istemciye | **Kısmen** | TC uyuşmazlığı → inceleme kuyruğu; inceleme satırları sayılmaz; genel hata mesajı. **Açık:** aynı dosyanın commit öncesi eşzamanlı ikinci yüklemesi (yarış) — batch `failed` olur, veri tutarlı kalır |
| 12 | Orta | Yetkinlik kapsama mükerrer sertifikaları sayıyordu | **Düzeltildi** | `count(DISTINCT competency_id)` |
| 13 | Orta | Plan sapması ve işe alım süresi meslek filtresini yok sayıyordu; plan versiyonları toplanıyordu | **Düzeltildi** | Meslek filtresi + yalnızca en son plan versiyonu |
| 14 | Orta | Asistan `forecast.read`'i atlıyordu; ret kalıpları yaygın ifadeleri kaçırıyordu ("Kovid" yanlış ret) | **Düzeltildi** | İzin kontrolü; genişletilmiş ve daraltılmış TR/EN/RU kalıpları · `test_f14_*` |
| 15 | Orta | Analitik uçlarda kapsam reddi denetlenmiyordu | **Düzeltildi** | `authz.scope_denied` kaydı · `test_f15_*` |
| 16 | Orta | Alt yüklenici görünürlüğü dönemin değil bugünün ilişkisine göre | **Açık — politika kararı** | ⚖️ Ürün/Hukuk: bugün verilen paylaşım izni geçmişi de açmalı mı? Seçenekler SECURITY_PRIVACY'ye eklendi |
| 17 | Orta | KPI-018 transferleri işe giriş/erken ayrılış sayıyordu | **Düzeltildi** | KPI-001/017 ile aynı transfer kuralı |
| 18 | Düşük-Orta | Yükleme boyutu okunduktan sonra kontrol; cp1254 CSV, bozuk XLSX → 500 | **Düzeltildi** | Akışlı sınırlı okuma (413), satır sınırı 200K, cp1254 geri dönüş, 400 · `test_f18_*` |
| 19 | Düşük | Eksik besleme gerçek sıfır gibi görünebiliyor (eğitim/izin/İSG); kurallar tutarsız | **Kısmen** | Devamlılık tutarlı hale getirildi. **Açık:** veri kaynağı kapsam meta verisi ("bu dönem için eğitim beslemesi var mı?") — B-07 ile |
| 20 | Düşük | Küçük sızıntılar/hijyen | **Kısmen** | Düzeltildi: `hidden_spells` kaldırıldı, geçmiş için 404, kaynak listesi `import.write`, JWT zorunlu claim'ler · `test_f20_*`. **Açık:** proje listesi kapsamsız, rate limit (G6), anket eşiği sabit, tahmin GET'inin kayıt yazması |

## Denetçinin "sorun yok" tespitleri
KPI önbellek anahtarı kapsam çözümlemesinden sonra üretiliyor (kullanıcılar arası yanlış paylaşım yok); SQL değerleri bağlı parametre; kapsam dışı istekler evaluate/series/anomaly/forecast/report/people uçlarında reddediliyor; scrypt, CORS, prod sır kontrolü yerinde.

## Teknik borç (kayıt altına alındı)
- Kapsam boyutları (proje/meslek) hesaplayıcılar arasında ortak bir filtre sözleşmesiyle değil tek tek uygulanıyor → "filtre uygulanmadı" bayrağı içeren ortak sözleşme (B-13 ile).
- KPI tanımları global; çok kiracılı formül override (B-09).
- Önbellek geçersizleştirmesi yazma yollarının `mark_stale_from` çağırmasına bağlı (sözleşme + test var; statik kontrol yok).
- Ham SQL hesaplayıcılarının indeks/EXPLAIN incelemesi yapılmadı.
- Asistan niyet tablosu sabit regex listesi (FAZ 6'da LLM niyet çözümleme + aynı yasak listesi).
- Örtüşen üretim standartları KPI-005'i çift sayabilir (şüpheli; veride örtüşme yok) → `production_standards` için dışlama kısıtı.
