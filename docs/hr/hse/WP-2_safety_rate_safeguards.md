# WP-2 — İSG oranı korkulukları, ölümlü kaza uyarısı, KPI-043 ramak kala ve bildirim kalitesi sinyalleri

> **Statü: Taslak — İK Direktörü incelemesi bekliyor.** Hazırlayan: Agent 18 (İSG Uzmanı). Onaylı değildir.
> Bu belge kod, katalog veya migration değiştirmez. Örnekler kurgusaldır. Sentetik demo veritabanı yalnızca ölçek
> kontrolü için okunmuştur (demo'da proje-ay çalışma saati ~10⁵ mertebesinde; bu yalnızca "küçük payda" sorununun
> gerçekçi olduğunu gösterir, şirket tabanı değildir). Çıktılar grup düzeyidir; kişi veya tekil olay değerlendirmesi yoktur.
> Dosya adı notu: `HR_TEAM_PLAN.md` WP-2 çıktıyı `HSE-02_safety_rate_guards_and_near_miss.md` olarak adlandırır; görevlendirme talimatıyla bu adla yazıldı.

| | |
|---|---|
| Tarih | 2026-11-05 |
| Sahip / inceleyen | Agent 18 → Agent 17 → Orchestrator |
| Girdiler | `docs/HR_DIRECTOR_RECOMMENDATIONS.md` §3.3, §5 #8–#9; `docs/AUDIT_REPORT.md` #3, #4, #19; `docs/SCREEN_MAP.md` (`/hse`); `backend/app/analytics/calculators.py` (`_safety_rate`, `ltifr`, `trir`, `_hours`); `backend/app/analytics/engine.py` (`_threshold_status`, `_payload` → `threshold_min_days`); `backend/app/analytics/domain.py` (`ratio`); `backend/app/analytics/catalog.py` (KPI-014A/B, KPI-043/044 taslak, `APPROVED_THRESHOLDS`, `FRAMEWORK_REQUIREMENTS`); `backend/app/models/operations.py` (`SafetyEvent`) |
| Uygulayacaklar (öneri) | A09 (motor kuralı, KPI-043, sinyaller), A07 (`/hse` etiket, uyarı bandı), A11 (görünürlük), A12 (testler); alanlar için WP-6 |

---

## 1. Kapsam ve ilgili backlog ID'leri

- **HR Director §5 #8:** İSG oranlarında küçük payda; ölümlü kaza için orandan bağımsız uyarı yok.
- **HR Director §5 #9:** KPI-014A/B için bildirim kalitesi göstergesi yok; KPI-043 taslak.
- **HR Director §3.3** bildirimi caydırmama kuralları 1–5 (eşiklerin onay koşulu).
- **AUDIT_REPORT #3/#4** (İSG oranlarında örneklem eşiği ve meslek filtresi — düzeltildi; bu belge onlara dokunmaz), **#19 / B-20** (eksik besleme ≠ sıfır).
- Bağımlılık: alanlar (`worker_category`, `reported_date`, vaka anahtarı) → `WP-6_safety_import_fields.md`; yeniden sınıflandırma sayaçları → `WP-1_work_injury_leave.md` §3.4.

## 2. Mevcut davranış (kod/doküman referansıyla)

- `_safety_rate`: pay = `safety_events` satır sayısı (tür listesi), payda = onaylı puantajda `worked_hours`; `ratio(..., sample_size=persons, min_sample=HRSWI_MIN_GROUP_SIZE)` → kişi sayısı < 5 ise **SUPPRESSED** (gizlilik). Maruziyet saati için bir alt sınır **yok**.
- `engine._payload`: `threshold_status` yalnızca formül parametresi `threshold_min_days` ile pencere uzunluğuna göre bastırılabiliyor (KPI-055/056'da 360). KPI-014A/B'de bu parametre yok → **aylık 80.000 saatlik bir proje-ayında tek LTI = 12,5 → kırmızı**.
- KPI-014A payı `lost_time_injury + fatality`; KPI-014B payı `fatality + lost_time_injury + restricted_work + medical_treatment`. Ölümlü kaza yalnızca oranın içinde bir sayı; ayrı uyarı yok. Oran gizlenirse/renk almazsa ölümlü kaza da görünmez kalabilir.
- `SafetyEvent` bir satır = bir olay; çoklu yaralanmalı olayda vaka sayısı belirsiz (WP-6 vaka düzeyi öneriyor). Olayın kime (kendi çalışanı / alt yüklenici / ziyaretçi) olduğu tutulmuyor.
- KPI-043 taslak: "Near-miss reports / hours worked x 200,000 (higher can mean better reporting culture)", yön `neutral`, hesaplayıcı yok. `FRAMEWORK_REQUIREMENTS["ISO45001"]` KPI-043'ü zaten kanıt olarak listeliyor.
- `/hse` ekranı: KPI-014A/B kutuları, KPI başına aylık grafik, firma tablosu (`SCREEN_MAP.md`).

## 3. Önerilen kural

### 3.1 Kural R1 — Renk için minimum maruziyet

**R1.a (zorunlu):** Bir İSG oranı için `threshold_status` yalnızca **değerlendirilen penceredeki çalışılan saat ≥ H_min** ise üretilir.

| KPI | H_min (öneri başlangıcı) | Kaynak |
|---|---|---|
| KPI-014A LTIFR | **1.000.000 sa** | HR Director §3.3; HR_TEAM_PLAN WP-2 |
| KPI-014B TRIR | **200.000 sa** (alternatif 500.000 — bkz. Q2) | Aynı |
| KPI-043 ramak kala (nötr, renk zaten yok) | 200.000 sa — yalnızca "düşük maruziyet" etiketi için | Bu belge |

- Eşik altında: **değer gösterilir, renk gösterilmez**, sonuç `status_reason = "low_exposure"` ve ekranda "Düşük maruziyet (X sa < H_min) — renk yok" etiketi taşır. **Olay sayıları her zaman değerin yanında gösterilir** (ör. "1 LTI / 80.000 sa").
- Sınır: `saat ≥ H_min` → renk var (eşitlik dahil).
- Gizlilik bastırması (kişi < 5) ayrı ve önceliklidir: SUPPRESSED sonuçta ne değer ne renk gösterilir; ancak ölümlü kaza uyarısı (R2) yine görünür.
- Uygulama önerisi: formül parametresi `threshold_min_exposure_hours` (KPI-014A: 1.000.000; KPI-014B: 200.000) — mevcut `threshold_min_days` deseninin aynısı; pay/payda zaten `CalcResult.denominator`'da. Parametre değişikliği → yeni formül versiyonu.

**R1.b (varsayılan görünüm):** `/hse` başlık kutuları ve yönetim raporları **12 ay kayan pencere (R12)** değerini, seçili dönem sonunda biten pencereyle gösterir; aylık grafik noktaları renksizdir (bir ay tek başına H_min'i aşmadıkça). HR Director §3.3 "12 aylık kayan pencere" okuma kuralının ekrana çevrilmiş halidir.

**R1.c (kayan pencere alternatifi — karar Agent 17'de):** R12'de H_min'e ulaşamayan küçük kapsamlar (ör. yılda 400.000 sa'lik proje) için:

| Seçenek | Kural | Artı / eksi |
|---|---|---|
| **C1 — Renk yok (öneri)** | Küçük kapsam hiç renk almaz; değer + sayı + "düşük maruziyet" etiketi. Renkli değerlendirme firma/tenant toplamında yapılır | Basit, istatistik olarak dürüst; küçük proje "yeşil" diye övülmez, "kırmızı" diye cezalandırılmaz |
| C2 — Genişleyen pencere | R12 < H_min ise pencere 3'er ay geriye genişletilir, en fazla 36 ay; H_min'e ulaşılırsa renk o pencereyle ve **"36 ay" gibi açık pencere etiketiyle** verilir | Küçük projeye de renk verir; ama eski dönem iyi performansı yeni bozulmayı gizleyebilir, proje fazları karışır |
| C3 — Yalnızca R12, saat şartsız | Plan metnindeki "veya 12 ay" okuması | **Önerilmez:** yılda 300.000 sa'lik projede tek LTI = 3,33 → sarı; tek olay hâlâ rengi belirler |

**Neden minimum saat — istatistik gerekçe (Poisson, olaylar bağımsız varsayımıyla, yaklaşık):**

| Kapsam / pencere | Gerçek oran tam hedefte iken | Yanlış "sarı veya kötü" olasılığı |
|---|---|---|
| LTIFR, 80.000 sa (proje-ay) | λ = 1,5 × 0,08 = 0,12 | Tek LTI ≥ 1 → %11,3 her ay; 12 ayda en az bir kırmızı ay **≈ %76** |
| LTIFR, 1.000.000 sa | λ = 1,5 | ≥ 3 LTI (sarı) **%19,1**; ≥ 5 (kırmızı) %1,9 |
| LTIFR, 2.000.000 sa | λ = 3 | ≥ 6 LTI (sarı) %8,4 |
| TRIR, 200.000 sa | λ = 1,0 | ≥ 2 vaka (sarı) **%26,4**; ≥ 4 (kırmızı) %1,9 |
| TRIR, 500.000 sa | λ = 2,5 | ≥ 5 vaka (sarı) %10,9 |
| TRIR, 1.000.000 sa | λ = 5 | ≥ 10 vaka (sarı) %3,2 |

Sonuç: minimum saat gürültüyü azaltır, **ortadan kaldırmaz**. Bu yüzden (i) olay sayısı her zaman gösterilir, (ii) sarı tek başına "kötüleşme" diye raporlanmaz, (iii) TRIR için 200.000 sa'in sarı yanlış alarm olasılığı LTIFR'ın 1M sa'ine göre yüksektir → Q2.

### 3.2 Kural R2 — Ölümlü kaza uyarısı (orandan bağımsız)

1. Kapsam × pencerede `event_type = fatality` sayısı > 0 ise sonuç yükünde **her zaman** bir uyarı nesnesi döner (ör. `alerts: [{"type": "fatality", "count": n, "by_worker_category": {...}}]`) ve `/hse` + yönetim panosunda oranın üstünde, kapatılamaz bir bant olarak gösterilir.
2. Uyarı; maruziyet kuralından (R1), oran gizlilik bastırmasından, meslek filtresinde oranın `insufficient_data` olmasından **etkilenmez**. Gösterilen bilgi: sayı, kapsam (firma/proje adı, yetkiye göre), dönem (ay düzeyi). **Kişi adı, yaş, meslek, olay açıklaması, neden, tanı yoktur.** ⚖️ (Agent 20 teyidi: ölümlü kaza sayısının küçük grupta bastırılmaması)
3. **Çalışan kategorisi** (WP-6): kendi çalışanı, alt yüklenici çalışanı, ziyaretçi/üçüncü kişi. Oranlara yalnızca çalışılan saati paydada olan kategoriler girer; **uyarı tüm kategorileri sayar.** Alt yüklenici ölümlü kazası, D-1 paylaşım izni olmasa bile ana yüklenicinin o projedeki `hse.read` kullanıcılarına proje düzeyinde "projede 1 ölümlü kaza" olarak görünmeli mi → Q4 (güvenli varsayılan: evet, firma adı olmadan).
4. Uyarının görünürlük süresi: penceresi olayı içeren her görünümde; R12 başlık görünümünde olaydan sonraki 12 ay.
5. Ölümlü kaydın düşürülmesi/silinmesi (ör. sonradan "işle ilgisiz doğal ölüm" kabulü ⚖️) yalnızca İSG yetkili rolünce, gerekçe kodu ve denetim izi ile yapılır; o pencerede **"1 ölümlü kayıt yeniden sınıflandırıldı"** sayısı uyarı bandında görünür kalır.
6. Uyarı, LTIFR rengini **zorla kırmızıya çevirmez** (oran matematiği dürüst kalır); LTIFR kutusunda "ölümlü kaza var" rozeti gösterilir.
7. Platform **"kazasız gün" / "son ölümlü kazadan beri geçen gün" sayacı göstermez** (sayaçlar bildirimi geciktirme ve gizleme baskısı yaratır).

### 3.3 Kural R3 — KPI-043 Ramak Kala Bildirim Oranı (aktivasyon spesifikasyonu)

| Alan | Spesifikasyon |
|---|---|
| Kod / ad | KPI-043 — Ramak Kala Bildirim Oranı / Near-Miss Reporting Rate / Частота сообщений о почти-происшествиях |
| Tanım | Yaralanma, meslek hastalığı veya ölüme yol açmamış, ancak çalışanı, işyerini veya ekipmanı zarara uğratma potansiyeli olan ve İSG sistemine bildirilen olaylar (6331 md. 14(1)(b) kayıt/inceleme yükümlülüğü ⚖️ teyit edilmeli; ISO 45001 madde 10.2 "olay" kavramı — teyit edilmeli) |
| Pay | Pencerede `event_date` olan `event_type = near_miss` kayıt sayısı (WP-6 kaynak anahtarıyla tekilleştirilmiş) |
| Payda | Aynı kapsam ve pencerede onaylı puantajda çalışılan saat (KPI-014A/B ile **aynı** payda; `_hours`) |
| Çarpan / birim | × 200.000 → "per 200k h" (KPI-014B ile aynı taban, yan yana okunabilir) |
| Yön | **`neutral`** |
| Eşik | **Yok** — gerçek verinin ilk 12 ayı boyunca kesin; 12 ay sonra da öneri `neutral` kalmasıdır (Q3) |
| Maruziyet | Saat < 200.000 → "düşük maruziyet" etiketi (renk zaten yok) |
| Kapsam filtreleri | Firma, proje; **meslek filtresinde `insufficient_data`** (KPI-014A/B ile aynı gerekçe) |
| Eksik veri | İSG beslemesinin o kapsam × ay için kapsam beyanı yoksa `insufficient_data`; beyan var ve kayıt yoksa **0** (B-20; WP-6 §3.6) |
| Kişi verisi | Bildirenin kimliği alınmaz; anonim bildirim geçerlidir. Kişi/ekip/ustabaşı kırılımı yok |
| Kullanım | KPI-014A/B eşiklerinin onay koşulu (HR Director §3.3 kural 2) ve R4 sinyallerinin girdisi; hiçbir prim/hedef/kota |
| Uygulama | `_safety_rate` aynı hesaplayıcı, `event_types=["near_miss"]`, `multiplier=200000` (yeni hesaplayıcı anahtarı veya `trir` parametreli kullanımı — A09 kararı); `status: active` → yeni formül versiyonu |

**Yön gerekçesi:**

| Yön | Etki | Karar |
|---|---|---|
| `lower_better` | Bildirimi doğrudan cezalandırır | **Asla** |
| `higher_better` (eşikli) | Kota/hedefe dönüşür: "haftada 5 ramak kala" → değersiz, uydurma veya bölünmüş bildirim; gerçek ramak kalalar sıradan gözlemler içinde kaybolur; ekipler arası yarış | Önerilmez (en az ilk 12 ay; sonra da yalnızca Agent 17 + İSG Kurulu kararıyla) |
| **`neutral` (öneri)** | Değer bağlam bilgisi; kötüleşme R4 sinyalleriyle (düşüş + oran düşüşü birlikte, sessizlik) yakalanır | **Öneri** |

### 3.4 Kural R4 — Bildirim kalitesi sinyalleri (renk değil, inceleme tetikleyicisi)

Ortak kurallar: yalnızca firma / proje / tenant kapsamında; kişi, ekip, ustabaşı kırılımı yok; hiçbir sinyal prim/değerlendirmeye bağlanmaz; sinyal metni "inceleme önerilir" der, "başarısız" demez; görünürlük `HSE_SPECIALIST`, `HR_DIRECTOR`, `OPERATIONS_DIRECTOR` (öneri, A11). Tüm eşik değerleri **yargıdır** (güven: düşük) ve gerçek veriyle 12 ay sonra kalibre edilir.

| # | Sinyal | Kural | Minimum veri |
|---|---|---|---|
| S1 | **Paralel düşüş** (HR Director §3.3 kural 2) | R12 ramak kala oranı önceki R12'ye göre **≥ %30 düşmüş** VE aynı dönemde LTIFR veya TRIR düşmüş → "iyileşme olarak raporlanmaz, veri kalitesi incelemesi" | Her iki R12'de saat ≥ 200.000 |
| S2 | **Bildirim sessizliği** | Ardışık 3 ayda `near_miss` = 0 VE `first_aid` = 0, kapsam beyanı var | 3 ayda saat ≥ 200.000 |
| S3 | **Piramit tutarsızlığı** (HR Director §3.3 "tutarlılık") | R12'de kaydedilebilir vaka sayısı / (LTI + ölümlü) sayısı **< 2** → tıbbi müdahalenin ilk yardıma düşürülmesi veya tersi şüphesi | LTI + ölümlü ≥ 3 |
| S4 | **Kısıtlı iş payı** (§3.3 kural 4) | R12'de RWC / (RWC + LTI) **> %50** → "hafif iş" görevlendirmesinin kayıp günlü kazayı gizleme amaçlı kullanımı incelemesi | RWC + LTI ≥ 4 |
| S5 | **Geç kayıt** | Vakalarda iç kayıt tarihi (`reported_date`, WP-6) olay tarihinden **3 iş gününden** sonra olanların payı > %10 (R12) | Vaka ≥ 10 |
| S6 | **Aşağı yönlü yeniden sınıflandırma** | Çeyrekte daha hafif sınıfa çevrilen vaka sayısı (ör. LTI → RWC, MTC → FAC, ölümlü → diğer) ve izin tarafında `work_injury → sick` (WP-1 §3.4) | Sayı gösterilir; kapsam < 5 ise firma/tenant toplamına yuvarlanır |
| S7 | **İzin ↔ olay uyumu** | WP-1 §3.5 (`R` ≠ `K`) | WP-1'deki gibi |

## 4. El hesaplı örnekler (tablo)

Eşikler: KPI-014A 1,5 / 3,0 / 5,0; KPI-014B 1,0 / 2,0 / 3,5 (lower_better; uyarıya eşit = sarı). Tüm sayılar kurgusal.

### 4.1 R1 — LTIFR

| # | Durum | Hesap | Değer | Bugün | Kural sonrası |
|---|---|---|---|---|---|
| A (plan) | Proje-ay 80.000 sa, 1 LTI | 1 / 80.000 × 1.000.000 | **12,5** | kırmızı | **renk yok**, "düşük maruziyet (80.000 < 1.000.000)", "1 LTI" |
| B (plan) | R12 1.050.000 sa, 2 LTI | 2 / 1.050.000 × 1.000.000 | **1,90** | yeşil | **yeşil** (1,90 < 3,0; renk kurallı) |
| C (sınır) | R12 999.999 sa, 2 LTI | 2 / 999.999 × 10⁶ | 2,00 | yeşil | **renk yok** (999.999 < 1.000.000) |
| D (sınır) | R12 tam 1.000.000 sa, 3 LTI | 3 / 1.000.000 × 10⁶ | **3,00** | sarı | **sarı** (saat = H_min → renk var; değer = uyarı → sarı) |
| E (C1/C2) | Küçük proje: R12 400.000 sa, 1 LTI; son 36 ay 1.200.000 sa, 2 LTI | R12: 1/400.000×10⁶ = 2,50; 36 ay: 2/1.200.000×10⁶ = 1,67 | 2,50 / 1,67 | yeşil (2,50 < 3,0) — tek LTI daha sarıya taşır | C1: **renk yok** (2,50 gösterilir); C2: **yeşil, "36 ay" etiketiyle** (1,67) |

### 4.2 R1 — TRIR

| # | Durum | Hesap | Değer | Kural sonrası |
|---|---|---|---|---|
| F | Ay 180.000 sa, 1 MTC | 1 / 180.000 × 200.000 | 1,11 | **renk yok** (< 200.000) |
| G | R12 2.100.000 sa, 15 kaydedilebilir | 15 / 2.100.000 × 200.000 | **1,43** | **yeşil** |
| H (sınır) | R12 210.000 sa, 4 kaydedilebilir | 4 / 210.000 × 200.000 | **3,81** | **kırmızı** (≥ 3,5; saat ≥ 200.000). 500.000 sa alternatifi seçilirse renk yok (Q2) |

### 4.3 R2 — Ölümlü kaza

| # | Durum | Oran | Uyarı |
|---|---|---|---|
| I | Proje-ay 80.000 sa, 1 ölümlü (kendi çalışanı), 0 LTI | LTIFR = 1/80.000×10⁶ = 12,5, **renk yok** (düşük maruziyet) | **Uyarı görünür:** "Bu dönemde 1 ölümlü kaza" + LTIFR kutusunda rozet |
| J (sınır) | Proje-ay, kapsamda 4 kişi çalışmış (gizlilik bastırması), 1 ölümlü (alt yüklenici çalışanı) | Oran **SUPPRESSED** | **Uyarı yine görünür** (sayı + proje; kişi bilgisi yok) |
| K (sınır) | 1 ölümlü, **ziyaretçi/üçüncü kişi** | LTIFR payına girmez (paydada saati yok) | **Uyarı görünür**, kategori "üçüncü kişi" |
| L | Ölümlü kayıt sonradan İSG yetkilisince yeniden sınıflandırıldı | Oran yeniden hesaplanır | Bant: "1 ölümlü kayıt yeniden sınıflandırıldı (gerekçe kodlu)" o pencerede kalır |

### 4.4 R3 — KPI-043

| # | Durum | Hesap | Sonuç |
|---|---|---|---|
| M | R12 1.050.000 sa, 63 ramak kala | 63 / 1.050.000 × 200.000 | **12,0** (nötr, renk yok) |
| N (sınır) | Ay 80.000 sa, 3 ramak kala | 3 / 80.000 × 200.000 | 7,5 + "düşük maruziyet" etiketi |
| O (sınır, B-20) | Ay için İSG kapsam beyanı **yok** | — | **`insufficient_data`** (0 değil) |
| P (sınır, B-20) | Kapsam beyanı var, 0 ramak kala, 90.000 sa | 0 / 90.000 × 200.000 | **0,0** (gerçek sıfır) |

### 4.5 R4 — Sinyaller

| # | Girdi | Hesap | Sinyal |
|---|---|---|---|
| S1-a | Önceki R12: 1.000.000 sa, 60 NM, 3 LTI. Şimdiki R12: 1.000.000 sa, 30 NM, 1 LTI | NM oranı 12,0 → 6,0 (−%50); LTIFR 3,0 → 1,0 (düşüş) | **Var** — LTIFR'daki düşüş "iyileşme" olarak raporlanmaz |
| S1-b (sınır) | Aynı, şimdiki NM 42 | 12,0 → 8,4 = −%30,0 | **Var** (≥ %30) |
| S1-c (sınır) | Aynı, şimdiki NM 45 | 12,0 → 9,0 = −%25 | Yok |
| S2 | 3 ay, 250.000 sa, 0 NM, 0 FA, kapsam beyanı var | — | **Var** |
| S2 (sınır) | 3 ay, 180.000 sa, 0 NM, 0 FA | saat < 200.000 | Değerlendirilmez |
| S3 | R12: LTI 3, kaydedilebilir 5 (LTI dahil) | 5 / 3 = 1,67 < 2 | **Var** |
| S3 (sınır) | R12: LTI 2, kaydedilebilir 3 | LTI + ölümlü < 3 | Değerlendirilmez |
| S4 | R12: RWC 6, LTI 4 | 6 / 10 = %60 > %50 | **Var** |
| S4 (sınır) | R12: RWC 5, LTI 5 | %50, "> %50" değil | Yok |
| S5 | 20 vaka, 3'ü 3 iş gününden geç kaydedilmiş | 3 / 20 = %15 > %10 | **Var** |

## 5. Kabul kriterleri

1. KPI-014A/B yeni formül versiyonları `threshold_min_exposure_hours` (1.000.000 / 200.000 veya Q2 kararı) taşır; Örnek A–D birebir: A → değer 12,5, `threshold_status = null`, `status_reason = "low_exposure"`; B → 1,90 `good`; C → `null`; D → 3,00 `warning`.
2. Eski formül versiyonuyla hesaplanmış/dondurulmuş sonuçlar değişmez (`test_formula_change_preserves_old_results` deseni).
3. `/hse` başlık kutuları R12 değerini gösterir; aylık grafik noktaları H_min altındayken renksizdir ve olay sayısı her değerin yanında görünür.
4. R1.c için seçilen seçenek (C1 öneri) uygulanır; C2 seçilirse pencere etiketi zorunludur.
5. Ölümlü kaza uyarısı Örnek I–L'de görünür; SUPPRESSED, `low_exposure`, meslek filtresi `insufficient_data` durumlarında da görünür; yanıt kişi kimliği, olay açıklaması veya gün düzeyinde tarih içermez.
6. Platformda hiçbir "kazasız gün" sayacı yoktur.
7. KPI-043: `status = active`, `direction = neutral`, eşikler NULL; Örnek M = 12,0; N "düşük maruziyet" etiketli; O `insufficient_data`; P 0,0.
8. Sinyaller S1–S5 Örnek tablosundaki her satırı birebir üretir (sınır satırları dahil); hiçbir sinyal kişi/ekip kırılımı döndürmez; sinyal yanıtı yalnızca izinli rollerde döner.
9. KPI-014A/B eşiklerinin "aktif" sayılması için KPI-043'ün aktif olması şartı katalog notunda yazılıdır (HR Director §3.3 kural 2).

## 6. Önerilen test adları

- `test_ltifr_no_colour_below_min_exposure` (A, C)
- `test_ltifr_colour_at_exact_min_exposure` (D)
- `test_ltifr_rolling_12m_colour` (B)
- `test_trir_min_exposure_rule` (F, G, H)
- `test_small_scope_no_colour_option_c1` / `test_expanding_window_label_option_c2`
- `test_event_counts_always_returned_with_rate`
- `test_fatality_alert_independent_of_rate_status` (I)
- `test_fatality_alert_visible_when_rate_suppressed` (J)
- `test_fatality_alert_counts_third_party` (K)
- `test_fatality_reclassification_keeps_marker` (L)
- `test_fatality_alert_has_no_person_fields`
- `test_kpi043_near_miss_rate_exact` (M)
- `test_kpi043_neutral_no_thresholds`
- `test_kpi043_missing_feed_is_insufficient_not_zero` (O, P)
- `test_signal_parallel_decline_threshold_boundary` (S1-a/b/c)
- `test_signal_reporting_silence_min_hours` (S2)
- `test_signal_pyramid_inconsistency` (S3)
- `test_signal_restricted_work_share_boundary` (S4)
- `test_signal_late_recording_share` (S5)
- `test_quality_signals_role_restricted_and_group_level`

## 7. Etkilenen sözleşmeler / ADR gerekli mi

**ADR gerekli — evet.**

| Sözleşme | Değişiklik |
|---|---|
| KPI sonuç yükü (`KpiResult` / `_payload`) | Ek alanlar: `status_reason` (`low_exposure`), `alerts` (ölümlü kaza), olay sayıları her zaman. Geriye uyumlu ekleme, ama ortak sözleşme → ADR + Orchestrator onayı |
| Formül parametresi `threshold_min_exposure_hours` | Motorda yeni genel kural (`threshold_min_days` gibi); KPI-014A/B yeni formül versiyonu |
| KPI-043 aktivasyonu | Katalog (A09) — yeni aktif KPI; `FRAMEWORK_REQUIREMENTS` zaten referans veriyor (A03 alanı, değişiklik gerekmez) |
| Bildirim kalitesi sinyalleri | Yeni uç nokta veya `/hse` yükünde ek blok (A06/A09); yeni izin gerekiyorsa A11 |
| WP-6 alanları | `worker_category`, `reported_date`, vaka anahtarı — S5, R2 kategori ve tekilleştirme bunlara bağlı → A05 migration |

## 8. Teşvik ve manipülasyon riski (bildirim karşıtı teşvik analizi)

| Kural | Olası bildirim karşıtı etki | Önlem |
|---|---|---|
| R1 renk yok | Yöneticiler kapsamı küçük projelere bölerek renkten kaçabilir | Renkli değerlendirme firma/tenant toplamında yapılır; küçük kapsam "yeşil" de almaz → bölmenin kazancı yok |
| R1 R12 görünümü | Kötü bir ayın etkisi 12 ay sürer → "bu ayı kayda geçirme" baskısı | Olay sayıları ve aylık değer her zaman görünür; prim/değerlendirme bağı yok (HR Director §3.3 kural 1) |
| R2 ölümlü kaza uyarısı | Ölümlü olayı "işle ilgisiz" diye sınıflandırma baskısı | Yeniden sınıflandırma yalnızca İSG yetkilisinde, gerekçe kodlu, işaret kalıcı (Örnek L); S6 sayacı |
| R2 sayaç yasağı | — | "Kazasız gün" sayaçları bildirimi geciktirme ve gizleme baskısı üretir; platform göstermez |
| R3 KPI-043 yönü | `higher_better` kota ve uydurma bildirim üretir; `lower_better` bildirimi cezalandırır | `neutral`, eşiksiz; kötüleşme S1/S2 ile |
| R4 sinyaller | Sinyaller şantiye şefini suçlamak için kullanılırsa bildirim düşer | Kişi/ekip kırılımı yok; "inceleme önerilir" dili; prim/disipline bağlanmaz; rol kısıtlı |
| S3/S4 | Sınıflandırmayı "ortalamaya" yaklaştırmak için yapay MTC/RWC üretme | Sınıflandırma tanımı WP-6'da nesnel kriterlere (gün, tıbbi müdahale listesi) bağlı; sinyal yalnızca inceleme tetikler |
| Bildirim kampanyası sonrası oran artışı | Artış "başarısızlık" sayılırsa kampanya yapılmaz | HR Director §3.3 kural 3: ilk 6–12 ayda artış başarısızlık sayılmaz — ekranda kampanya notu eklenebilir |

## 9. ⚖️ maddeler

- ⚖️ **6331 md. 14** — iş kazası/meslek hastalığı kaydı ve bildirimi; (1)(b) bendinin ramak kala niteliğindeki olayların incelenip raporlanmasını kapsadığı — **teyit edilmeli**.
- ⚖️ **5510 md. 13** — iş kazasının SGK'ya 3 iş günü içinde bildirimi (S5 iç kayıt süresi bu süreden bağımsızdır; platform SGK bildirimine karar vermez) — **teyit edilmeli**.
- ⚖️ "Ramak kala olay" tanımının yer aldığı İSG yönetmeliği (İSG Hizmetleri / Risk Değerlendirmesi yönetmeliklerinden hangisi) — **teyit edilmeli**.
- ⚖️ İşle ilgisiz kabul edilen ölümler (doğal ölüm vb.) ve işveren servis aracında trafik kazası — 5510 md. 13 kapsamı; uyarı kapsamına alınması/çıkarılması — **hukukçu ve İSG profesyoneli**.
- ⚖️ KVKK md. 6 — ölümlü kaza sayısının küçük grupta bastırılmaması (güvenlik yararı) ve vefat etmiş kişiye ilişkin verinin statüsü — **Agent 20 / KVKK irtibat kişisi**.
- ⚖️ 6331 md. 22 (İSG Kurulu) ve asıl işveren–alt işveren koordinasyonu: alt yüklenici ölümlü kazasının paylaşım izni olmadan ana yüklenici kullanıcılarına gösterilmesi (Q4) — **teyit edilmeli**.
- Uluslararası referanslar (yasal değil, tanım kaynağı): GRI 403-9 (2018) oran tabanı 200.000 veya 1.000.000 sa ve çalışan olmayan işçiler; ESRS S1-14 (ölümler, kaydedilebilir kaza oranı 1.000.000 sa başına, kayıp gün); ISO 45001 madde 5.4 (çalışan katılımı, bildirim engellerinin kaldırılması) ve 10.2; OSHA 29 CFR 1904.35 (misilleme yasağı, bildirimi caydıran teşvik programları); ILO "Recording and notification of occupational accidents and diseases" uygulama kodu (1996) ve 16. ICLS kararı (1998) — madde numaraları **teyit edilmeli**.

## 10. Açık sorular (Agent 17 için)

- **Q1 — R1.c:** küçük kapsamlar için C1 (renk yok, öneri) mi, C2 (36 aya kadar genişleyen, etiketli) mi?
- **Q2 — TRIR H_min:** 200.000 sa (onaylı §3.3 değeri; sarı yanlış alarm ≈ %26) mı, 500.000 sa (≈ %11, LTIFR 1M ile benzer) mı?
- **Q3 — KPI-043 yönü:** 12 ay sonra da `neutral` kalması (öneri) onaylanıyor mu?
- **Q4 — Alt yüklenici ölümlü kazası:** paylaşım izni (D-1) olmasa da proje düzeyinde ana yüklenicinin `hse.read` kullanıcılarına gösterilsin mi (güvenli varsayılan: evet, firma adı olmadan)?
- **Q5 — S1–S5 eşikleri** (%30, 3 ay, < 2, > %50, > %10) başlangıç için kabul mü? Hepsi yargıdır.
- **Q6 — "Tehlikeli durum / tehlikeli davranış" gözlemleri** KPI-043'e girmesin (öneri); ayrı ileride izleme göstergesi mi olsun?
- **Q7 — Ölümlü kaza uyarısı görünürlüğü:** `kpi.read` olan ama `hse.read` olmayan yönetici rolleri (ör. salt okunur yönetici) de görsün mü? Öneri: evet, KPI-014A'yı görebilen herkes.

## 11. İnsan İSG profesyoneli / hukukçu gerektirenler

- H_min, R12 ve S1–S5 değerlerinin şirket prosedürüne alınması ve İSG Kurulunda (6331 md. 22) görüşülmesi.
- Ölümlü kayıtların yeniden sınıflandırma gerekçe kodlarının (işle ilgisiz ölüm vb.) belirlenmesi — iş güvenliği uzmanı + hukuk.
- Müşteri/işveren sözleşmelerindeki İSG oranı tanımlarının (taban 200k/1M, alt yüklenici dahil mi) bu kurallarla uyumu — sözleşme yönetimi.

## 12. Statü

**Taslak — İK Direktörü incelemesi bekliyor.** Onaylı değildir.

---

## İK Direktörü incelemesi (2026-11-05)

**İnceleyen:** Agent 17 (İK Direktörü) · **Karar: ŞARTLI ONAY** · Ölçütler: `HR_TEAM_PLAN.md` §2.4 ve §4.2.

### A. El hesaplarının ve istatistik gerekçenin yeniden hesaplanması

| Örnek | Benim hesabım | Sonuç |
|---|---|---|
| A | 1/80.000 × 10⁶ = 12,50 | ✔ |
| B | 2/1.050.000 × 10⁶ = 1,905 → `good` (< 3,0) | ✔ |
| C / D | 2/999.999 × 10⁶ = 2,000002 (renk yok); 3/1.000.000 × 10⁶ = 3,00 = uyarı → `warning` | ✔ |
| E | 1/400.000 × 10⁶ = 2,50; 2/1.200.000 × 10⁶ = 1,667 | ✔ |
| G / H | 15/2.100.000 × 200.000 = 1,429 (`good`); 4/210.000 × 200.000 = 3,810 (≥ 3,5 `critical`) | ✔ |
| M / N | 63/1.050.000 × 200.000 = 12,00; 3/80.000 × 200.000 = 7,50 | ✔ |
| S1-a/b/c | NM oranı 12,0 → 6,0 (−%50) / 8,4 (−%30) / 9,0 (−%25) | ✔ (koşul 4: sınır hesabı) |
| Poisson tablosu | λ=0,12: 1−e^−0,12 = %11,3; 12 ay: 1−e^−1,44 = %76,3. λ=1,5: P(≥3) = %19,1, P(≥5) = %1,9. λ=3: P(≥6) = %8,4. λ=1: P(≥2) = %26,4, P(≥4) = %1,9. λ=2,5: P(≥5) = %10,9. λ=5: P(≥10) = %3,2 | ✔ tamamı |

### B. Onay koşulları

1. **Tek renk bastırma mekanizması:** R1'in `status_reason = "low_exposure"` alanı, WP-5'in KPI-020 bantlarındaki "değer var, renk yok" işaretiyle (`threshold_suppressed`) ve mevcut `threshold_min_days` davranışıyla **aynı** genel mekanizma olarak tasarlanır: `threshold_status = null` + `status_reason ∈ {short_window, low_exposure, unknown_category_share, low_coverage}`. Tek ADR (bkz. `SPRINT1_REVIEW.md` ADR-a). İki ayrı bayrak icat edilmez.
2. **TRIR H_min = 500.000 sa** (Q2 kararı, aşağıda); kabul kriteri 1 ve Örnek H buna göre: H (210.000 sa) → **renk yok**; G değişmez. Örnek F değişmez.
3. **S5 "iş günü" tanımı yazılır:** Pazartesi–Cuma, ülkenin resmî tatilleri hariç (WP-4 `public_holidays` tablosu). Bu bir iç kayıt sinyalidir; SGK bildirim süresine uyum yargısı **değildir** (⚖️ OHS-03). WP-6 İSG Örnek 4.1 satır 2 bu tanımla tutarlıdır (5 iş günü > 3).
4. **S1 sınırı deterministik hesaplanır:** yüzde değişim kayan noktalı orandan değil, `NM_now × H_prev ≤ 0,70 × NM_prev × H_now` biçiminde tam sayı/Decimal karşılaştırmasıyla değerlendirilir; aksi halde S1-b (tam −%30) yuvarlama hatasıyla "yok" çıkabilir.
5. Ölümlü kaza uyarısı (R2) **kapsam kapısından da bağımsızdır** (WP-6 veri §3.6 `insufficient_data` durumunda da görünür) — R2 madde 2'ye eklenir.
6. Kabul kriteri 9'daki "KPI-014A/B eşiklerinin aktif sayılması için KPI-043'ün aktif olması şartı" katalog notu olarak yazılır; eşik **değerleri** (D-5) değişmez.
7. R2 madde 3 ve Q4 (alt yüklenici ölümlü kazası, paylaşım izni olmadan) **ürün sahibi kararına** gider (D-1 istisnası). Karar gelene kadar güvenli varsayılan uygulanır: proje düzeyinde yalnızca **sayı**, firma adı yok.

### C. Açık sorulara kararlarım

| # | Karar | Gerekçe |
|---|---|---|
| Q1 | **C1 — küçük kapsam renk almaz**; renkli değerlendirme firma/tenant toplamında | Dürüst ve basit; C2'nin 36 aylık penceresi proje fazlarını karıştırır ve yeni bozulmayı eski iyi dönemle örter |
| Q2 | **TRIR H_min = 500.000 sa** (200.000 değil) | 200.000 sa'te hedefteki bir kapsamın yanlış sarı alma olasılığı ≈ %26; 500.000 sa'te ≈ %11 — LTIFR'ın 1.000.000 sa'teki ≈ %19'uyla aynı mertebe. Tek olayın rengi belirlemesi, kaza gizleme baskısının ana kaynağıdır. Yaklaşık 200 kişilik bir proje (≈ 2.600 sa/kişi/yıl) R12'de bu eşiği aşar. Güven: orta (yargı). Bu, kendi önceki "öneri başlangıcı" değerimin revizyonudur; D-5 eşik değerleri değişmez |
| Q3 | **Evet — KPI-043 kalıcı olarak `neutral`** | `higher_better` kota ve uydurma bildirim üretir; değişiklik ancak İSG Kurulu görüşüyle (OHS-08) ve İK Direktörü kararıyla gündeme gelir |
| Q4 | Güvenli varsayılan: **evet, proje düzeyinde yalnızca sayı, firma adı yok** — nihai karar ürün sahibi + WP-3 OHS-07(d) | Ölümlü kaza gizlenemez; asıl işverenin koordinasyon yükümlülüğü (6331 md. 23 ⚖️ teyit edilmeli) |
| Q5 | S1–S5 başlangıç değerleri **kabul**, güven düşük; gerçek veride 12 ay sonra kalibre | Hepsi renk değil inceleme tetikleyicisi; yanlış pozitifin bedeli düşük |
| Q6 | **Evet — tehlikeli durum/davranış gözlemleri KPI-043'e girmez**; ayrı gösterge şimdilik yok | Ramak kala tanımını sulandırır; gözlem kartı kotası riski |
| Q7 | **Evet — KPI-014A'yı o kapsamda görebilen herkes ölümlü kaza bandını görür** (sayı + proje + ay; kişi bilgisi yok) | Ölümlü kazanın hat yönetiminden saklanması güvenlik kültürüne aykırı; eklenen tanımlama riski sahada zaten bilinen bilgiye göre ihmal edilebilir. **Not:** WP-3 OHS-07 güvenli varsayılanı ("yalnızca 3 rol") bu kararla hizalanır ve hukukçuya bu haliyle sorulur |

### D. Çapraz tutarlılık

- S6 sayacı WP-1 §3.4 ve WP-6 İSG V15 ile aynı kural kodlarını kullanır (`reclassified_up/down`) — WP-1 koşul 5.
- R3 "eksik veri" satırı WP-6 veri §3.6 kapısına bağlıdır; İSG için `min_feed_coverage = 0,9` (WP-6 veri kararı).
- `worker_category` değerleri WP-6 İSG §3.3 ile aynıdır.

### E. Statü

**İK Direktörü onaylı (şartlı)** — koşullar 1–7. R1 + R2 (kategorisiz) ilk uygulama dalgasına alınabilir; KPI-043 ve R2 kategori kırılımı WP-6 İSG import'una, S1–S7 sinyalleri 12 aylık veriye bağlıdır. İnsan onayı: İSG profesyoneli ve İSG Kurulu (H_min, S1–S5 değerlerinin prosedüre alınması — OHS-08), ürün sahibi (Q4).
