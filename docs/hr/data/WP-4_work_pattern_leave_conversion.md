# WP-4 — Çalışma Düzenine Göre İzin Günü Çevrimi ve Sözleşmesel İzin Hakkı

> **Statü: Taslak — İK Direktörü incelemesi bekliyor.** Yazan: Agent 19 (İK Veri ve Metrik Spesifikasyon Analisti).
> Kod, katalog veya migration değiştirmez; mühendislik için spesifikasyondur. Tüm örnekler **kurgusaldır**; hiçbir
> sayı şirketin gerçek verisine veya sentetik demo veriye dayanmaz. Hukuki yorum değildir (⚖️ maddeler Agent 20'ye).

| | |
|---|---|
| Tarih | 2026-11-05 |
| İş paketi | WP-4 (`docs/HR_TEAM_PLAN.md` §3). Plandaki çıktı adı `DATA-01_work_pattern_and_leave_entitlement.md`; Orchestrator talimatıyla bu adla yazıldı. Örnek veri: `docs/hr/data/WP-4_examples.csv` |
| Girdiler | B-28, `HR_DIRECTOR_RECOMMENDATIONS.md` Ek E.2 düzeltme 1–5 ve §5 #2, D-3/D-5/D-6/D-7, `calculators.py` (`_annual_leave_population`, `annual_leave_usage`, `annual_leave_nonuse`, `absenteeism_rate`, `protected_leave_share`, `_hours`), `catalog.py` (KPI-003/028/053/054/055/056), `services/imports.py` (`LEAVE_FIELDS`, `LEAVE_HOURS_PER_DAY`, `_working_days`), `models/operations.py` (`WorkCalendar`, `Timesheet`, `LeaveRecord`), `INTEGRATION_CONTRACTS.md` §6.1 |
| Uygulayan (onaydan sonra) | A05 (tablolar + migration), A08 (import alanları ve çevrim), A09 (KPI-055/056 ve KPI-003/028/053/054 yeni formül versiyonları), A12 (CSV → fixture), A07 (`/imports` uyarıları) |

---

## 1. Kapsam ve ilgili backlog ID'leri

- **B-28** (P1): izin günü çevrimi çalışma düzenine göre; TİS/sözleşmeden doğan ek izin hakkı (KPI-055 paydası) ⚖️.
- **§5 #2**: izinli günlerde puantajda planlı saatin yazılıp yazılmaması → KPI-003/028/053/054 paydası.
- **Ek E.2 düzeltmeleri 1–5**: `/9` sabiti, md. 54 kıdem, hak doğum tarihi, uzun yokluk, hak kaynağı.
- Kapsam dışı: rotasyonlu yurt dışı düzenler (ör. 10 hafta saha / 2 hafta ev) — v1'de "düzen bilinmiyor" sayılır
  (bkz. §10 S-4); yarım gün/saatlik izin politikaları; B-30 paneli.

## 2. Mevcut davranış (kod/doküman referansıyla)

| # | Yer | Bugünkü davranış | Sonuç |
|---|---|---|---|
| M1 | `calculators._annual_leave_population` | `taken = annual_h / hours_per_day`, `hours_per_day` katalogda **sabit 9** (KPI-055/056 `params`) | 6 günlük (7,5 sa/gün) düzende tam kullanılan izin eksik görünür (E.2 #1) |
| M2 | aynı | Hak = yalnızca md. 53 yasal süre (kıdem bandı + yaş ≤18/≥50 → min 20) | TİS/sözleşme ile fazla gün veren işyerinde oran yapay biçimde >100 (E.2 #5) |
| M3 | aynı | Kıdem = aynı `legal_entity_id`'deki tüm dönemlerin gün **toplamı** | md. 54 yönünde doğru; ancak (a) **çakışan** iki dönem çift sayılır, (b) platforma yüklenmemiş eski dönemler (geçmiş verinin başladığı tarihten önce) hiç sayılmaz |
| M4 | aynı | `long_absent`: sick/parental/unpaid saat `/9` > pencerenin 5/7'sinin yarısı | 6 günlük düzende eşik ve gün sayısı yanlış ölçekte |
| M5 | `imports._validate_leave` | Saat: `hours` → yoksa `days × 9` → yoksa **Pzt–Cum** gün × 9 (`hours_derived` uyarısı). `days` değeri **saklanmaz** | Gün bilgisi kaynakta olsa bile kaybolur; 6 günlük düzende saat şişer (12 gün × 9 = 108 sa; doğrusu 90 sa); resmî tatil düşülmez |
| M6 | `LeaveRecord` | Yalnızca `hours`; gün, gün dayanağı ve düzen alanı yok | KPI-055'in "izinli iş günü sayımı" yapılamaz |
| M7 | `WorkCalendar` | Tenant düzeyi `weekly_hours=45`; günlük saat / haftalık gün / geçerlilik tarihi yok; kişi veya şantiyeye bağlı değil | Düzen bilgisi KPI'lara ulaşmıyor |
| M8 | `absenteeism_rate`, `protected_leave_share` | Payda = onaylı puantajdaki `planned_hours` toplamı | Kaynak izinli günde planlı saat yazmıyorsa payda düşer, oran şişer (§5 #2) |
| M9 | `absenteeism_rate` | Pencere sınırını aşan izin **takvim günü** oranıyla bölüştürülür | Küçük sapma; gün tabanlı çevrimde iş günü oranı daha doğru |

## 3. Önerilen kural

### 3.1 Veri modeli — gerekli alanlar (A05)

**(a) `work_patterns`** — çalışma düzeni kataloğu (tenant düzeyi)

| Alan | Tip | Zorunlu | Açıklama / doğrulama |
|---|---|---|---|
| `code` | str(40) | ✔ | Ör. `TR-5x9`, `TR-6x7.5`. Tenant içinde tekil |
| `days_per_week` | int 1–7 | ✔ | Haftalık iş günü sayısı |
| `working_weekdays` | int listesi (ISO 1=Pzt…7=Paz) | ✔ | Uzunluğu `days_per_week`'e eşit olmalı; ör. 6 gün → `[1,2,3,4,5,6]` |
| `daily_hours` | numeric(4,2) | ✔ | Ör. 9,00 / 7,50. > 11 → hata (md. 63 günlük üst sınır ⚖️) |
| `weekly_hours` | numeric(5,2) | ✔ | = `days_per_week × daily_hours` (±0,01) değilse hata; TR'de > 45 → uyarı ⚖️ (denkleştirme olabilir) |
| `country` | str(2) | ✔ | Resmî tatil takvimi seçimi |
| `pattern_type` | enum `weekly` | ✔ | v1 yalnızca haftalık; rotasyon düzenleri desteklenmez |
| `source_system`, `source_record_id` | | | Kaynak: SAP HCM IT0007 (Work Schedule Rule), SuccessFactors Work Schedule, Logo/Netsis "puantaj grubu / çalışma takvimi" |

**(b) `work_pattern_assignments`** — etkin tarihli bağlama (`EffectiveDatedMixin`, `[valid_from, valid_to)`)

| Alan | Tip | Açıklama |
|---|---|---|
| `work_pattern_id` | FK | |
| `level` | enum `employment` \| `project` \| `legal_entity` | Hangi düzeyde tanımlı |
| `employment_id` / `project_id` / `legal_entity_id` | FK (yalnızca `level`'e karşılık gelen dolu) | CHECK: tam olarak biri dolu |
| `valid_from`, `valid_to` | date | Aynı hedefte çakışan aralık → hata |

**Çözümleme sırası (tarih bazında):** kişi (`employment`) → kişinin o tarihteki birincil görevlendirmesinin
projesi (`project`) → işveren (`legal_entity`) → **bulunamadı = `pattern_unknown`**. Tenant'ın `work_calendars`
kaydı (45 sa) **varsayım**dır, bilinen düzen sayılmaz (yalnızca geri dönüş böleni 9 için kullanılır).

**(c) `public_holidays`** — ülke × tarih × `fraction` (1,0 tam gün; 0,5 arife yarım günü) × ad. Kaynak resmî
takvim (TR: 2429 sayılı Kanun ⚖️ güncel metin teyit; RU ayrı). Bir ülke-yıl için takvim yoksa → `holiday_calendar_missing`.

**(d) `leave_entitlement_rules`** — yasal tabanın üstündeki hak (etkin tarihli)

| Alan | Tip | Açıklama |
|---|---|---|
| `scope_level` | enum `employment` \| `collective_agreement` \| `legal_entity` | Bireysel sözleşme / TİS / işveren politikası |
| `scope_ref` | FK veya TİS kodu | `collective_agreement` için `employment_relationships.collective_agreement_code` ile eşleşir |
| `rule_type` | enum `extra_days` \| `band_table` | `extra_days`: yasal süreye +N gün; `band_table`: kıdem bandı → gün tablosu (JSON) |
| `value` | numeric / JSON | |
| `valid_from`, `valid_to` | date | |
| `source_document` | str | TİS no / sözleşme maddesi — **zorunlu** (denetim izi) |
| `approved_by`, `approved_at` | | İK yönetimi onayı olmadan kural etkin değildir |

**(e) `employment_relationships`'e eklenecek alanlar**

| Alan | Tip | Açıklama |
|---|---|---|
| `collective_agreement_code` | str(40), null | TİS kapsamı (null = kapsam dışı veya bilinmiyor; ayrım için `collective_agreement_known` bool) |
| `leave_seniority_date` | date, null | **İzne esas kıdem tarihi** (bordro sistemlerindeki "izin hak ediş başlangıç / kıdem tarihi"). md. 54 birleştirmesi kaynakta yapılmışsa, önceki dönemlerin platformda olmadığı durumda kıdemi taşır. `> start_date` → hata |

**(f) `leave_records`'a eklenecek alanlar**

| Alan | Tip | Açıklama |
|---|---|---|
| `days` | numeric(6,2), null | İzinli **iş günü** sayısı (yarım gün desteklenir) |
| `day_basis` | enum `source_days` \| `pattern_counted` \| `hours_divided` \| `default_9h` | Gün değerinin nasıl üretildiği (kalite puanı ve denetim için) |
| `work_pattern_id` | FK, null | Çevrimde kullanılan düzen (izin başlangıcında çözümlenen) |

### 3.2 Gün çevrimi kuralları (import'ta, A08) — tercih edilen yöntem "izinli iş günü sayımı"

Her izin satırı için, başlangıç tarihinde çözümlenen düzen `P` ve ülke takvimi `H` ile:

1. **Kaynak gün verdiyse** (`days` dolu): `days = kaynak değer`, `day_basis = source_days`.
   Doğrulama: `days > iş_günü_say(P, H, başlangıç, bitiş)` → uyarı `days_exceed_pattern_working_days`.
2. **Kaynak gün vermediyse, tarih aralığı var**: `days = iş_günü_say(P, H, başlangıç, bitiş)` =
   aralıktaki `P.working_weekdays` günlerinin sayısı − resmî tatil `fraction`'ları (md. 56 son fıkra: hafta tatili ve
   genel tatiller izinden sayılmaz ⚖️), `day_basis = pattern_counted`, uyarı `days_derived_from_pattern`.
3. **Saat**: `hours` kaynakta varsa aynen; yoksa `hours = days × P.daily_hours` (uyarı `hours_derived_from_pattern`).
4. **Yalnızca saat var, tarih aralığı gün sayımına uygun değil (geri dönüş yöntemi "düzene göre bölen")**:
   `days = hours / P.daily_hours`, `day_basis = hours_divided`.
5. **Düzen bilinmiyor** (`pattern_unknown`): bugünkü davranış korunur — `days = hours / 9` veya Pzt–Cum sayımı,
   `day_basis = default_9h`, uyarı `pattern_unknown_default_9h`. Satır **reddedilmez**, ama KPI-055/056'da kapsam
   oranına girer (§3.4).
6. Ülke-yıl için tatil takvimi yoksa adım 2'de tatil düşülmez + uyarı `holiday_calendar_missing`.
7. Tür eşlemesi değişmez: `normalize_leave_type` önce korunan izni `parental`'a eşler (**D-6**). Çevrim tür
   bağımsızdır; WP-1 (Agent 18) `work_injury` türünü eklerse aynı çevrim uygulanır.

**"Veri yok" ↔ "gerçek sıfır":** Bir kişinin dönemde izin kaydı olmaması, ancak o dönem için izin beslemesi
**tam** beyan edilmişse (WP-6 kapsam meta verisi) gerçek sıfırdır (`taken = 0`). İzin beslemesi o dönem/işveren için
yoksa KPI-055/056 `insufficient_data` döner; sıfır gösterilmez.

### 3.3 Hak (payda) kuralları — KPI-055

- **Kıdem (md. 54 ⚖️):** `service_days` =
  - `leave_seniority_date` doluysa: `pe − leave_seniority_date + 1` (kaynaktaki birleştirilmiş kıdem esas alınır);
  - değilse: aynı işverendeki tüm dönemlerin **birleşimi** (çakışan aralıklar bir kez sayılır; bugünkü toplam yerine);
  - Tutarlılık kontrolü: iki yöntem arasında > 30 gün fark → uyarı `seniority_mismatch` (yalnızca İK Operasyon
    düzeltme kuyruğunda; panoda grup sayısı).
  - En eski yüklü dönem, tenant'ın geçmiş veri başlangıç tarihine denk geliyor ve `leave_seniority_date` yoksa →
    `seniority_possibly_truncated` (kalite puanını düşürür).
- **Yasal hak (md. 53):** mevcut bantlar korunur (≤5 yıl 14, 5–15 yıl 20, ≥15 yıl 26; yaş ≤18 veya ≥50 → en az 20).
- **Sözleşmesel hak:** geçerli kuralların her biri için gün hesaplanır (`extra_days` → yasal + N; `band_table` →
  tablo). **`entitlement = max(yasal, tüm geçerli sözleşmesel kurallar)`** — sözleşme yasal tabanın altına indiremez
  (güvenli varsayılan, ⚖️ L-4).
- **Orantılama:** mevcut kural korunur: `max(ps, one_year_date)`'ten `pe`'ye gün / 365.
- **Uygunluk (D-7):** değişmez — ≥1 yıl hizmet, kişi dönem sonunda aktif.

### 3.4 KPI-055 / KPI-056 — yeni formül versiyonu (A09)

| | v1 (bugün) | v2 (öneri) |
|---|---|---|
| Pay (KPI-055) | Σ yıllık izin saati / 9 | Σ yıllık izin `days` (pencereyle çakışan kısım; düzen biliniyorsa **iş günü** oranıyla, değilse takvim günüyle bölüştürülür) |
| Payda (KPI-055) | Σ yasal hak × orantı | Σ `max(yasal, sözleşmesel)` × orantı |
| Kıdem | dönem gün toplamı | `leave_seniority_date` → yoksa dönem **birleşimi** |
| KPI-056 uzun yokluk | (sick+parental+unpaid saat)/9 > pencere×5/7/2 | (aynı türlerin `days` toplamı) > kişinin düzenine göre pencere iş günü / 2 (5 gün: ×5/7, 6 gün: ×6/7). WP-1 onaylanırsa `work_injury` da bu listeye girer |
| Kapsam | yok | `pattern_coverage` = uygun nüfusta düzeni bilinen kişi oranı. `< min_coverage` (öneri **0,8**, mevcut `PARAM_RULES.min_coverage` anahtarı) → `insufficient_data`, `quality_score = kapsam×100` |
| Kalite puanı | doğum yılı bilinen oranı | min(doğum yılı bilinen oranı, `pattern_coverage`, kıdemi kesilmemiş oranı) × 100 |
| Lineage | `hours_per_day` | `day_basis` dağılımı (sayı), `pattern_coverage`, `contractual_rule_count`, `seniority_truncated` sayısı |

Parametreler: `day_basis: "leave_days"`, `min_coverage: 0.8`, `threshold_min_days: 360` (mevcut). `hours_per_day`
v2'de yalnızca `default_9h` satırlarının geri dönüş böleni olarak kalır. **v1 sonuçları değişmez** (formül satırları
değiştirilemez, `INTEGRATION_CONTRACTS.md` §3/5 ve §9).

### 3.5 KPI-003 / 028 / 053 / 054 — pay ve payda (§5 #2)

**Pay:** formül değişmez; ancak import'ta saat türetimi 3.2'ye göre düzen bazlı olur (veri düzeltmesi). Geçmiş
kayıtlar için tek seferlik yeniden çevrim (yalnızca `hours` kaynakta **verilmemiş** satırlar) + `mark_stale_from`.

**Payda — iki kaynak senaryosu:**

| Senaryo | Kaynağın davranışı | Bugünkü payda | Doğru payda |
|---|---|---|---|
| **A — "dahil"** | İzinli günde puantaj satırı var, `planned_hours` = düzen saati (ör. 7,5), `worked_hours` = 0 | Puantaj planlı saati — doğru | Değişmez |
| **B — "hariç"** | İzinli günde satır yok veya `planned_hours` = 0 | Planlı saat izin kadar eksik → oran **şişer** | Puantaj planlı saati **+** izin saatleri (resmî tatil hariç tüm türler, pencereye bölüştürülmüş) = dönem planlı kapasite saati |

- Senaryo, puantaj beslemesinin kapsam satırında (WP-6 §3.4) `planned_on_leave_days ∈ {included, excluded, unknown}`
  özniteliği olarak **beyan edilir** (kaynak × işveren × dönem).
- **Otomatik kontrol (A08):** günlük puantajda, izinli iş günlerinin kaçında `planned_hours > 0` satır var →
  oran ≥ %90 "included", ≤ %10 "excluded", arası "mixed". Beyan ≠ gözlem → uyarı `planned_on_leave_mismatch`.
  Haftalık/aylık özet satırlarda tespit yapılamaz → gözlem "unknown".
- **Yeni formül versiyonu** (KPI-003, 028, 053, 054): parametre `denominator_basis: "planned_capacity"`. Hesap:
  `included` → bugünkü payda; `excluded` → payda + izin saatleri; `unknown`/`mixed` → bugünkü payda,
  `quality_score = 50`, not: "izinli günlerde planlı saat yazımı doğrulanmadı; oran olduğundan yüksek olabilir".
- **D-3/D-5/D-6 korunur:** tür setleri (`absence_types`) değişmez; `parental` hiçbir zaman KPI-003/028/053 payına
  girmez; yalnızca KPI-054'ün payıdır. Payda ekine `parental` saatleri de girer (kapasite kaybıdır) — bu, korunan
  izni devamsızlığa **eklemez**, yalnızca paydayı düzeltir.

## 4. El hesaplı örnekler (tablo)

Tümü kurgusal. Pencere aksi yazılmadıkça 12 ay: `ps = 2025-11-01`, `pe = 2026-10-31` (365 gün); kişiler tüm pencere
boyunca uygun (orantı 365/365). Makine okunur kopya: `WP-4_examples.csv`.

### 4.1 KPI-055 — kişi düzeyi girdi, grup düzeyi sonuç (örnekler tek kişilik "grup" gibi gösterilmiştir; gerçek sonuç `HRSWI_MIN_GROUP_SIZE` altında bastırılır)

| # | Durum | Girdi | Bugün (v1) | Kural sonrası (v2) |
|---|---|---|---|---|
| **E1** | **6 günlük şantiye** (7,5 sa/gün), hak 14 gün, tam kullanım | 105 sa yıllık izin (kaynak gün vermemiş) | 105 / 9 = 11,67 gün → 11,67 / 14 = **%83,3** (sarı değil ama hedefin altında; birim yanlış) | 105 / 7,5 = 14 gün (`hours_divided`) veya tarih aralığında 14 iş günü (`pattern_counted`) → 14 / 14 = **%100** |
| **E2** | **5 günlük kontrol** (9 sa/gün), hak 14 gün, tam kullanım | 126 sa (= 14 × 9) | 126 / 9 = 14 → **%100** | 126 / 9 = 14 → **%100** (değişmez ✔) |
| **E3** | **Yeniden işe alınmış** (md. 54): 1. dönem 2019-03-01–2022-02-28 (1.096 gün), 2. dönem 2023-06-01–açık (`pe`'ye 1.249 gün); 6 günlük şantiye; 150 sa izin | (a) iki dönem yüklü; (b) 1. dönem platformda yok, `leave_seniority_date` yok; (c) 1. dönem yok, `leave_seniority_date = 2020-05-31` | (a) 2.345 gün = 6,42 yıl → hak 20; 150/9 = 16,67 → **%83,3**; (b) 1.249 gün = 3,42 yıl → hak 14; 16,67/14 = **%119,0** (yanlış "iyi") | (a) hak 20; 150/7,5 = 20 → **%100**; (b) hak 14 → 20/14 = **%142,9** + `seniority_possibly_truncated` → kalite düşer; (c) `pe − 2020-05-31 + 1 = 2.345` gün → hak 20 → **%100** |
| **E4** | **TİS ek hakkı**: 1–5 yıl kıdem için TİS "+4 gün" → 18 gün; 6 günlük şantiye | (a) 135 sa (18 gün) kullanmış; (b) 105 sa (14 gün) kullanmış | (a) 135/9 = 15 / 14 = **%107,1** (yapay >100); (b) 105/9 = 11,67 / 14 = **%83,3** | (a) 18 / 18 = **%100**; (b) 14 / 18 = **%77,8** (hedef 90'ın altında; uyarı 75'in üstünde → `good`) |
| **E5** | **Düzen değişikliği** (sınır): 2026-05-01'den itibaren 5 → 6 gün | Mart'ta 5 iş günü (45 sa, 5×9); Ağustos'ta 6 iş günü Pzt–Cmt (45 sa, 6×7,5) | 90 / 9 = **10 gün** | 5 + 6 = **11 gün** (her satır kendi başlangıç tarihindeki düzenle) |
| **E6** | **Resmî tatil + arife** (sınır): izin 2026-10-26 (Pzt) – 2026-11-07 (Cmt); 29 Ekim tam gün, 28 Ekim arife 0,5 | Kaynak yalnızca tarih vermiş | Pzt–Cum sayımı: 10 gün × 9 = 90 sa → 10 gün (tatil düşülmez) — **her iki düzende aynı** | 6 gün: 13 takvim − 1 Pazar − 1 (29 Ekim) − 0,5 (arife) = **10,5 gün**, 10,5 × 7,5 = 78,75 sa. 5 gün: 10 − 1 − 0,5 = **8,5 gün**, 8,5 × 9 = 76,5 sa. Arifenin 0,5 sayılması ⚖️ L-2 |
| **E7** | **Eksik veri** (grup): 10 uygun kişi, 3'ünün düzeni hiçbir düzeyde tanımlı değil | `min_coverage = 0,8` | Değer hesaplanır, kalite yalnızca doğum yılına bakar (ör. 100) | `pattern_coverage = 7/10 = 0,70 < 0,80` → **`insufficient_data`**, `quality_score = 70`, not: "3 kişinin çalışma düzeni bilinmiyor". 1 kişi bilinmeseydi: 0,90 → değer hesaplanır (o kişi `/9` ile), kalite 90 |
| **E8** | **Gerçek sıfır ↔ veri yok** | Uygun 8 kişi, düzen bilinen; dönemde hiç yıllık izin kaydı yok | Her iki durumda da KPI-055 = **%0**, KPI-056 = **%100** | (a) İzin beslemesi 12 ayın tamamı için "tam" beyanlı → **%0 / %100 (gerçek sıfır, kırmızı)**; (b) besleme beyanı yok/eksik ay → **`insufficient_data`** (WP-6 kapsam kuralı) |

**Kıdem birleşimi sınır örneği (M3a):** aynı işverende dönem A 2024-01-01–2024-12-31 (366 gün) ve eşzamanlı dönem B
2024-07-01–2025-06-30 (365 gün). Bugün: toplam **731 gün** (2,0 yıl). Kural: birleşim 2024-01-01–2025-06-30 = **547 gün**
(1,5 yıl). Bu kişi bugün yanlışlıkla 2. yılını doldurmuş, kural sonrası 1,5 yılında görünür.

### 4.2 KPI-003 / 053 / 054 paydası (§5 #2)

Kurgusal şantiye, 6 günlük düzen, ayda 24 iş günü × 7,5 = 180 sa/kişi.

| # | Durum | Senaryo A (dahil) | Senaryo B (hariç) — bugün | Senaryo B — kural sonrası |
|---|---|---|---|---|
| **E9** | 10 kişi; bir kişi 6 gün yıllık (45 sa), bir kişi 2 gün raporlu (15 sa) → izin 60 sa | Payda 1.800 → KPI-003 = 60/1.800 = **%3,33**; KPI-053 = 15/1.800 = **%0,83** | Payda 1.740 → KPI-003 **%3,45**; KPI-053 **%0,86** | Payda 1.740 + 60 = 1.800 → **%3,33** / **%0,83** (A ile aynı ✔) |
| **E10** | 50 kişi; 5 kişi ay boyunca `parental` (5 × 180 = 900 sa) | Payda 9.000 → KPI-054 = **%10,0** | Payda 8.100 → KPI-054 = **%11,1** | 8.100 + 900 = 9.000 → **%10,0**. KPI-003/053 paylarına `parental` girmez (D-6) |
| **E11** | Kaynak puantajı haftalık özet satırı (tespit yapılamaz), beyan yok | — | Bugünkü payda, kalite 100 | Bugünkü payda, **`quality_score = 50`** + not (oran yüksek olabilir) |
| **E12** | Pay türetimi: 6 günlük şantiyede kaynak raporlu izni yalnızca **gün** olarak vermiş (12 gün) | — | 12 × 9 = **108 sa** (KPI-028/053 payı şişer) | 12 × 7,5 = **90 sa** |

## 5. Kabul kriterleri

1. Düzen çözümleme sırası (kişi → proje → işveren → bilinmiyor) etkin tarihli çalışır; tenant `work_calendars`
   varsayımı "bilinen düzen" sayılmaz.
2. E1–E8 ve E9–E12 değerleri birebir (2 ondalık) üretilir; E2 (5 günlük kontrol) v1 ile v2'de aynıdır.
3. Kaynakta verilen `hours` ve `days` hiçbir zaman ezilmez; türetilen her değer `day_basis` ve uyarı koduyla izlenir.
4. `entitlement` hiçbir zaman yasal sürenin altına düşmez; sözleşmesel kural `source_document` ve onay olmadan etkin olmaz.
5. Kıdem: çakışan dönemler bir kez sayılır; `leave_seniority_date` varsa öncelik onda; fark > 30 gün uyarı üretir.
6. `pattern_coverage < 0,8` → KPI-055/056 `insufficient_data`; hiçbir eksik besleme `0` olarak gösterilmez.
7. KPI-003/028/053/054 v2 paydası beyan edilen senaryoya göre hesaplanır; `unknown` → kalite 50 + not.
8. v1 formül sonuçları korunur (`test_formula_change_preserves_old_results` deseni); KPI-055/056 12 ay altı pencerede renk yok (D-7).
9. Hiçbir çıktı kişi listesi/sıralaması üretmez; tüm sonuçlar `HRSWI_MIN_GROUP_SIZE` bastırmasına tabidir.
10. `normalize_leave_type` önceliği (korunan izin → `parental`) değişmez (D-6).

## 6. Önerilen test adları (A12)

- `test_six_day_site_leave_hours_converted_with_7_5_hours_per_day` (E1)
- `test_five_day_pattern_annual_leave_usage_unchanged_between_v1_and_v2` (E2)
- `test_rehire_service_combined_md54_and_seniority_date_override` (E3 a/b/c)
- `test_overlapping_spells_same_employer_counted_once` (M3a)
- `test_contractual_entitlement_used_when_higher_never_below_statutory` (E4)
- `test_pattern_change_mid_period_converts_each_record_with_its_own_pattern` (E5)
- `test_public_holiday_and_eve_half_day_not_counted_as_leave_day` (E6)
- `test_pattern_coverage_below_min_returns_insufficient_data` (E7)
- `test_annual_leave_true_zero_only_with_complete_leave_feed` (E8, WP-6 ile)
- `test_absence_denominator_adds_leave_hours_when_source_excludes_leave_days` (E9, E10)
- `test_absence_denominator_unknown_profile_halves_quality_score` (E11)
- `test_leave_import_days_only_derives_hours_from_pattern` (E12)
- `test_leave_import_keeps_source_days_and_flags_days_exceeding_pattern`
- `test_pattern_resolution_order_employment_project_entity_unknown`
- `test_planned_on_leave_mismatch_warning`
- `test_protected_leave_still_parental_after_pattern_conversion` (D-6 regresyon)
- `test_kpi055_v1_results_preserved_after_v2`

## 7. Etkilenen sözleşmeler / ADR gerekli mi

**ADR gerekli — evet.** Gerekçe:
- Yeni tablolar (`work_patterns`, `work_pattern_assignments`, `public_holidays`, `leave_entitlement_rules`) ve
  `employment_relationships` / `leave_records` kolonları → yeni Alembic revizyonu (eski revizyon değişmez).
- `INTEGRATION_CONTRACTS.md` §6.1 (izin import'u — "saat: `days × 9`" kuralı) ve §6 (istihdam import'una
  `collective_agreement_code`, `leave_seniority_date`) değişir.
- Yeni formül versiyonları: KPI-055/056 (yeni parametreler `day_basis`, `min_coverage`), KPI-003/028/053/054
  (`denominator_basis`). `engine.PARAM_RULES`'a yeni anahtarlar eklenir (ortak sözleşme).
- `KpiResult` şekli değişmez (yeni bilgiler `lineage`/`breakdown` içinde).
- Geçmiş izin kayıtlarının yeniden çevrimi `mark_stale_from` çağırmalıdır (§5).
- Bağımlılık: E8 ve "veri yok" kuralı WP-6 kapsam meta verisine bağlıdır; iki ADR birlikte değerlendirilmeli.

## 8. Teşvik ve manipülasyon riski

| Risk | Etki | Kontrol önerisi |
|---|---|---|
| 5 günlük şantiyeyi 6 günlük beyan etmek | Saat/7,5 → daha çok gün → KPI-055 yapay yükselir | Düzen–puantaj tutarlılık kontrolü: 5 günlük düzende haftaların ≥ %50'sinde cumartesi planlı saat varsa (veya tersi) `pattern_timesheet_mismatch` (grup sayısı); düzen değişiklikleri denetim izli |
| TİS ek gününü sisteme girmemek | Payda küçük kalır, KPI-055 iyi görünür | TİS kapsamı (`collective_agreement_code`) dolu ama kural yok → `entitlement_rule_missing` uyarısı; kurallar sendika/TİS belgesine referanslı |
| `leave_seniority_date`'i geriye çekmek/ileri almak | Hak bandı değişir | Spell birleşimiyle > 30 gün fark → uyarı; değişiklik denetim izli |
| Düzeni bilerek boş bırakmak (kötü sonucu `insufficient_data` ile gizlemek) | Renk kaybolur | `insufficient_data` kendisi panoda "veri eksik" olarak görünür ve İK veri kalitesi listesine düşer; kapsam oranı trendi izlenir |
| Gün değil saat göndermek / tersini | Çevrim seçimi | Her iki değer gelirse ikisi de saklanır; `hours ≠ days × daily_hours` (±%10) → uyarı `hours_days_inconsistent` |
| Tek günlük parçalarla %90'a ulaşma (E.1) | Dinlenme sağlanmaz | Bu WP kapsamı dışı; "≥10 gün kesintisiz izin" izleme göstergesi ileride (E.3 #9) |

## 9. ⚖️ maddeler (Agent 20'ye soru olarak aktarılır — hukuki yorum değildir)

| # | Kapalı uçlu soru | Güvenli varsayılan (yanıta kadar) |
|---|---|---|
| L-1 | 5 günlük düzende çalışılmayan cumartesi yıllık izin süresinden **sayılır mı**? (md. 56 son fıkra, Yıllık Ücretli İzin Yönetmeliği — güncel metin teyit) | Sayılmaz (yalnızca düzenin iş günleri sayılır; çalışan lehine). Kaynak `days` verirse kaynak değeri esas alınır |
| L-2 | Arife yarım günü (ör. 28 Ekim öğleden sonra) izin gününden **0,5 düşülür mü**? | 0,5 düşülür (çalışan lehine) |
| L-3 | md. 53'teki yer altı işleri için +4 gün **tünel işlerine uygulanır mı**? (E.2 #5) | Uygulanmaz (kural tanımlanmaz) ama tünel projeleri `leave_entitlement_rules` ile işaretlenebilir; hukuk yanıtı "evet" ise `extra_days = 4` kuralı açılır |
| L-4 | TİS/sözleşme yasal süreden **az** gün öngörürse yasal süre mi uygulanır? Birden çok kural varsa en lehe olan mı? | `max(yasal, tüm sözleşmesel)` |
| L-5 | md. 54 birleştirmesi: aynı grup içinde **farklı tüzel kişilikler** arası geçişte (işyeri devri md. 6 dahil) kıdem birleşir mi? | Platform yalnızca aynı `legal_entity` dönemlerini birleştirir; `leave_seniority_date` kaynakta birleştirilmişse ona uyulur |
| L-6 | Kısmi süreli çalışanda (md. 13) izin günü kendi iş günleri üzerinden mi sayılır? | Kişinin düzenindeki iş günleri sayılır; günlük saat düzenden |
| L-7 | 6 günlük düzende haftalık 45 saati aşan düzen beyanları (denkleştirme) izin çevrimini etkiler mi? | `daily_hours` beyanı esas; > 45 sa/hafta uyarı |

## 10. Açık sorular (İK Direktörü, Agent 17)

- **S-1:** `min_coverage = 0,8` (KPI-055/056 düzen kapsamı) uygun mu? Daha düşük (0,6) ilk 6 ayda geçiş için kullanılabilir.
- **S-2:** Senaryo `unknown` için kalite 50 + not yeterli mi, yoksa `insufficient_data` mı istenir? (Ben not + düşük kaliteyi
  öneriyorum; aksi halde düzen beyanı gelene kadar tüm devamsızlık KPI'ları kaybolur.)
- **S-3:** 1 yılı doldurmadan kullandırılan "avans izin" KPI-055 nüfusuna girmez (D-7 uygunluk kuralı). Ayrıca izlenmeli mi?
- **S-4:** Yurt dışı rotasyon düzenleri (E.3 #7) v2'de "bilinmiyor" sayılıyor; bunlar için ayrı düzen tipi (sprint 2) gerekli mi?
- **S-5:** Geçmiş izin kayıtlarının yeniden çevrimi hangi tarihten itibaren yapılmalı (tüm geçmiş mi, kapanmamış dönemler mi)?
  Kapalı dönemlerin değişmesi D-2 dönem kapatma kuralıyla çakışır → öneri: yalnızca açık dönemler; kapalı dönemler v1 sonuçlarıyla kalır.

## 11. Statü

**Taslak — İK Direktörü incelemesi bekliyor.** (İK Direktörü incelemesi bölümü bu satırın altına eklenecek.)
