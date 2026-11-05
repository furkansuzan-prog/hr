# WP-5 — Devir Göstergelerinde Planlı Sözleşme/Proje Sonu, SGK Çıkış Kodu Eşlemesi ve 12 Ay Kuralı

> **Statü: Taslak — İK Direktörü incelemesi bekliyor.** Yazan: Agent 19 (İK Veri ve Metrik Spesifikasyon Analisti).
> Kod, katalog veya migration değiştirmez. Örnekler **kurgusaldır**; sentetik demo veriden yalnızca yapı kontrolü
> yapılmıştır, hiçbir demo değeri şirket tabanı olarak sunulmaz. Hukuki yorum değildir (⚖️ → Agent 20).

| | |
|---|---|
| Tarih | 2026-11-05 |
| İş paketi | WP-5 (`docs/HR_TEAM_PLAN.md` §3). Plandaki çıktı adı `DATA-02_turnover_definitions.md`; Orchestrator talimatıyla bu adla yazıldı. Eşleme tablosu (fixture adayı): `docs/hr/data/WP-5_sgk_exit_code_map.csv` |
| Girdiler | §5 #3, #4, #12; §3.1; KPI-001/002/018/020/021/022 (`catalog.py`, `docs/KPI_CATALOG.md`); `calculators.py` (`turnover_rate`, `_separations`, `retention_rate`, `early_turnover_90`); `engine.py` (`PARAM_RULES`, `_payload` yıllıklaştırma ve `threshold_min_days`); `models/people.py` (`TERMINATION_CATEGORIES`, `termination_reason_code`, `contract_type`); `services/imports.py` (`FIELDS`, `CONTRACT_TYPES`); `INTEGRATION_CONTRACTS.md` §6 |
| Uygulayan (onaydan sonra) | A09 (yeni formül versiyonları + motor), A08 (çıkış kodu eşlemesi, import alanları), A05 (alanlar/tablo), A11 (sağlıkla ilgili kodların saklanmaması), A12 |

---

## 1. Kapsam ve ilgili backlog ID'leri

- **§5 #3** — KPI-018/021/022 kasıtlı kısa sözleşmelerin planlı bitişini erken ayrılış sayıyor.
- **§5 #4** — KPI-002 dönem uzunluğuna bağlı, yıllıklaştırılmıyor; aylık pencerede hep yeşil.
- **§5 #12** — KPI-020'de boş (bilinmeyen) ayrılış kategorisi payı veri kalitesine yeterince yansımıyor.
- **§6.1 / §3.1** — SGK işten çıkış kodlarının platform kategorilerine onaylı eşlemesi.
- WP-6 ile bağlantı: istihdam beslemesinin kapsam meta verisi ("bu dönem için ayrılışlar yüklendi mi").
- Kapsam dışı: KPI-001 tanımı (tüm ayrılışlar — değişmiyor), KPI-023 kıdem tanımı.

## 2. Mevcut davranış (kod/doküman referansıyla)

| # | Yer | Bugünkü davranış | Sorun |
|---|---|---|---|
| M1 | `early_turnover_90` (KPI-018/021/022) | Pay = kohortta `end_date < start_date + days` olan ve 7 gün içinde kapsam içi yeni döneme geçmeyen; **ayrılış kategorisine hiç bakmaz** | Planlı proje/sözleşme sonu erken ayrılış sayılır |
| M2 | `engine.PARAM_RULES` | `exclude_categories` anahtarı **küresel** olarak geçerli; `early_turnover_90` bu parametreyi **okumaz** | Orchestrator yalnızca katalog parametresi eklerse doğrulama geçer ama **sonuç değişmez** (sessiz etkisizlik). Hesaplayıcı değişikliği şart |
| M3 | `turnover_rate` (KPI-001/020) | `exclude_categories` / `include_categories` destekli; kalite = 1 − bilinmeyen / ayrılış; KPI-020'de bilinmeyen sayılmaz ve not düşülür | Bilinmeyen payı ne kadar yüksek olursa olsun renk üretilir (§5 #12) |
| M4 | `retention_rate` (KPI-002) | Yıllıklaştırma yok, `threshold_min_days` parametresi yok | 1 aylık pencerede ~%97 → her zaman yeşil (sahte rahatlık) |
| M5 | `engine._payload` | `annualize=True` → **doğrusal** `değer × 365 / gün` | KPI-002'ye uygulanırsa yanlış (%97 × 12 ≈ %1.180). Devamlılık doğrusal ölçeklenmez |
| M6 | `imports.FIELDS` (istihdam) | `termination_category` yalnızca platform değerleri; **SGK çıkış kodu alanı yok** (model kolonu `termination_reason_code` var ama import etmiyor) | Kaynak sistemin yasal bildirimi (SGK kodu) platforma ulaşmıyor |
| M7 | `imports.validate` | `contract_type` boşsa **sessizce `permanent`** | Eksik veri varsayılan değere dönüşüyor (kural 2'ye aykırı); planlı bitiş doğrulamasını bozar |
| M8 | Model | Planlanan sözleşme bitiş tarihi (`planned_end_date`) alanı yok | "Planlı bitişte mi ayrıldı?" sorusu yanıtlanamaz |

Sentetik demo veride (yalnızca yapı) `permanent` sözleşmeli ve kategorisi `contract_end` olan kayıtlar vardır; aşağıdaki
tutarlılık kontrolü bu kombinasyonu yakalar. Gerçek veride kaç kayıt yakalanacağı **bilinmiyor**.

## 3. Önerilen kural

### 3.1 Veri modeli ve import (A05, A08)

**`employment_relationships`:**

| Alan | Tip | Açıklama |
|---|---|---|
| `termination_reason_code` (mevcut) | str(40) | SGK işten çıkış kodu (2 hane, baştaki sıfır korunur). **Sağlıkla ilgili kodlar saklanmaz** (aşağıda) |
| `termination_reason_source` | enum `sgk` \| `hris` \| `manual` | |
| `planned_end_date` | date, null | Belirli süreli / proje sözleşmesinde sözleşmedeki planlanan bitiş |
| `planned_end_verified` | bool, null | Import'ta 3.3 kurallarıyla türetilir (true / false / null = değerlendirilemedi) |
| `contract_type` | mevcut + **`unknown`** değeri | Boş kaynak değeri artık `permanent` değil `unknown` + uyarı `contract_type_missing` |

**`termination_code_map`** (eşleme veridir, kod değildir): `code_system` (`SGK_ISTEN_CIKIS`), `code`, `label_tr`,
`category` (∈ `TERMINATION_CATEGORIES`), `planned_end_eligible` (bool), `health_related` (bool), `valid_from/to`,
`verified_by`, `verified_at`. **Doğrulanmamış (verified_at boş) eşleme satırı kullanılmaz** → kod `unknown`'a düşer.
Önerilen başlangıç içeriği: `WP-5_sgk_exit_code_map.csv` — kod listesi **bellekten derlenmiştir; SGK e-Bildirge
"işten ayrılış nedenleri" güncel listesiyle satır satır teyit edilmeden etkinleştirilmez** (ör. 29 kodunun 42–50 alt
kodlarına bölünmesi, 30–34 açıklamaları).

**İstihdam import'una eklenecek alanlar** (`FIELDS` deseni):

| Alan | Zorunlu | Takma adlar (TR/EN/RU) | Doğrulama |
|---|---|---|---|
| `sgk_exit_code` | Hayır (çıkış varsa **önerilir**) | "sgk çıkış kodu", "işten çıkış kodu", "çıkış kodu", "ayrılış kodu", "exit code", "код увольнения" | 1–2 hane sayısal; tek haneyse başına 0; eşleme tablosunda yok → uyarı `unknown_exit_code`, kategori `unknown` |
| `planned_end_date` | Hayır | "sözleşme bitiş", "planlanan bitiş", "planned end", "дата окончания договора" | `≥ start_date`; `contract_type = permanent` ise dolu olmamalı → uyarı `planned_end_on_permanent` |

**Kategori belirleme sırası (import):**
1. `sgk_exit_code` geçerli ve doğrulanmış eşlemede varsa → `category = eşleme`. Kaynak `termination_category`
   farklıysa **SGK kodu kazanır** (yasal bildirim), uyarı `category_code_conflict`.
2. Kod yoksa → kaynak `termination_category` (bugünkü gibi).
3. İkisi de yoksa ve `end_date` doluysa → `unknown` + `termination_reason_missing` (bugünkü gibi).
4. `health_related = true` kodlarda (09, 24, 28) kategori yazılır, **kod saklanmaz** (`termination_reason_code = NULL`,
   `termination_reason_source = sgk`). Ham değer yalnızca doğrulama sırasında bellekte kullanılır (KVKK md. 6 ⚖️ L-8).
5. Kod 16 (nakil): 7 gün içinde aynı kişinin yeni dönemi yoksa uyarı `transfer_without_new_spell`.
6. Kod 11 (iş kazası sonucu ölüm): İSG olay beslemesinde aynı işveren-ayda `fatality` olayı yoksa grup düzeyi
   tutarlılık uyarısı `fatality_not_in_hse_feed` (alanlar Agent 18'in İSG spesifikasyonunda; kişi bilgisi taşınmaz).

### 3.2 Platform kategorileri — eşleme özeti

| Platform kategorisi | SGK kodları (öneri, teyit edilmeli) | KPI etkisi |
|---|---|---|
| `voluntary` | 02, 03, 23, 24, 25, 33 | KPI-020 payı; KPI-001 |
| `employer_initiated` | 01, 04, 15, 26, 27, 28, 29, 32, 34, 42–50 | KPI-001; erken ayrılışta **sayılır** |
| `contract_end` | 05, 19, 20 | Planlı bitiş adayı (3.3) |
| `project_end` | 17, 18 | Planlı bitiş adayı (3.3) — inşaatta şantiye sicil kapanışı / işin bitimi |
| `retirement` | 08, 09, 14 | KPI-001 |
| `death` | 10, 11 | KPI-001 |
| `other` | 12, 13, 16, 21, 22, 30, 31 | KPI-001; 22 "diğer nedenler" düşük bilgi payında izlenir |
| `unknown` | kod/kategori yok veya listede olmayan kod | Veri kalitesi (3.5) |

### 3.3 Planlı bitiş doğrulaması (`planned_end_verified`)

Bir ayrılış **planlı bitiş** sayılır (`true`) ancak hepsi sağlanırsa:

| # | Koşul | Değerlendirilemezse |
|---|---|---|
| C1 | `category ∈ {contract_end, project_end}` | — (değilse `false`) |
| C2 | `contract_type ∈ {fixed_term, project, seasonal}` | `unknown` → `null` |
| C3 | SGK kodu varsa `planned_end_eligible = true` (05, 17, 18, 19, 20) | kod yoksa koşul atlanır |
| C4 | `planned_end_date` varsa `end_date ≥ planned_end_date − 7 gün` | tarih yoksa koşul atlanır |

C1 sağlanıp C2–C4'ten biri açıkça başarısızsa → `false` + uyarı (`contract_end_on_permanent`, `planned_end_mismatch`).
C2 değerlendirilemiyorsa → `null` (doğrulanamadı).

**"Veri yok" ↔ "gerçek sıfır":** `null` hiçbir zaman "planlı" sayılmaz; kişi erken ayrılış payında **kalır** (temkinli),
ve kalite puanına yansır. Kohortta hiç erken ayrılış yoksa ve kohort tam gözlenmişse değer **gerçek sıfırdır**.

### 3.4 KPI-018 / 021 / 022 — yeni formül versiyonu (v2)

- **Pay:** kohortta `end_date < start_date + days` olan, 7 gün içinde kapsam içi yeni döneme geçmeyen **ve
  `planned_end_verified IS NOT TRUE`** olan dönemler.
- **Payda:** değişmez (kohorttaki tüm işe girişler; planlı bitişle ayrılanlar da kohortta kalır — işe alındılar; soru
  "planlanmamış erken kayıp oranı"dır).
- **Parametre:** `planned_end_exclusion: "verified"` (seçenekler: `none` = v1, `category` = yalnızca C1, `verified` =
  C1–C4; öneri `verified`). `PARAM_RULES`'a eklenir; **ayrıca hesaplayıcı başına parametre beyaz listesi** önerilir
  (M2'deki sessiz etkisizliği kalıcı olarak önler — denetim #7'nin devamı).
- **Ayrı izlenen pay (zorunlu, yan etki kontrolü):** `breakdown.planned_end_excluded_share` =
  dışlanan / kohort × 100; `lineage`: `planned_end_excluded`, `planned_end_unverified_counted`,
  `category_code_conflicts`, `repeated_short_fixed_term_persons` (aynı kişi, aynı işveren, 12 ayda ≥ 2 planlı bitiş).
  Pay, KPI değeriyle aynı yayın kuralına tabidir (kohort ≥ `HRSWI_MIN_GROUP_SIZE`); kategori bazlı sayılar mevcut
  `suppress_small_counts` ile maskelenir.
- **Alarm (motor, grup düzeyi):** kayan 3 dönemde KPI-018 düşerken `planned_end_excluded_share` ≥ 10 puan artarsa
  not: "erken ayrılıştaki düşüş, planlı bitiş kodlamasındaki artışla birlikte — veri kalitesi kontrolü" (İSG'deki
  "oran düşüşü + bildirim düşüşü" mantığının aynısı).
- **Kalite puanı:** `min(gözlem_kalitesi, sınıflandırma_kalitesi)`; gözlem kalitesi mevcut kural (kohort tam
  gözlenmediyse 50), sınıflandırma kalitesi = 100 × (1 − (kategorisi `unknown` veya `planned_end_verified IS NULL`
  olup kategorisi planlı-bitiş adayı olan erken ayrılış) / v1 erken ayrılış sayısı).

### 3.5 KPI-020 — bilinmeyen kategori bantları (yeni formül versiyonu)

`u = unknown / tüm ayrılışlar` (dönem, kapsam). Parametre `unknown_share_bands: [0.05, 0.20]`:

| Bant | Davranış |
|---|---|
| `u ≤ 0,05` | Bugünkü gibi: değer + renk; kalite = 100 × (1 − u) |
| `0,05 < u ≤ 0,20` | Değer gösterilir, **renk üretilmez**; `breakdown.voluntary_range` = [gönüllü / ort. kişi, (gönüllü + bilinmeyen) / ort. kişi] × 100 (yıllıklaştırılmış karşılıklarıyla); not: "kategori bilgisi eksik; gerçek oran bu aralıkta" |
| `u > 0,20` | `insufficient_data` (değer yok), kalite = 100 × (1 − u) |

"Renk yok ama değer var" durumu hesaplayıcıdan motora bir işaretle (ör. `CalcResult.threshold_suppressed`) iletilmelidir —
**motor değişikliği → ADR**. Ayrıca veri kalite uyarısı: `(unknown + other) / tüm ayrılışlar > 0,15` → "düşük bilgi payı".
KPI-001 değişmez (tüm kategoriler sayılır); bilinmeyen payı yalnızca kalite puanına yansımaya devam eder.

### 3.6 KPI-002 — 12 ay kuralı (yeni formül versiyonu, yalnızca parametre)

- Parametre `threshold_min_days: 360` (KPI-055/056 ile aynı; motor zaten destekliyor) → 360 günden kısa pencerede
  `threshold_status = null`, not: "Devamlılık yalnızca 12 aylık pencerede değerlendirilir".
- **Yıllıklaştırma yapılmaz** (`annualize` eklenmez — M5). Bilgi amaçlı bileşik karşılık (aylık %97 →
  0,97^(365/30) ≈ %69) yalnızca dokümantasyonda gerekçe olarak kullanılır; kapalı nüfus ve sabit ayrılma hızı
  varsayar, ekranda gösterilmez.
- Hesaplayıcı değişmez → v1 ile v2 değeri aynıdır, yalnızca renk davranışı değişir.

## 4. El hesaplı örnekler (tablo)

### 4.1 KPI-018/021/022 — kohort 2026-01-01–2026-03-31, 10 işe giriş (tamamı kurgusal), gözlem tarihi 2026-11-05 (180 gün dahil tam gözlenmiş)

| Kişi | Giriş | Çıkış | Kategori | SGK | Sözleşme | Planlanan bitiş | Gün | <30 | <90 | <180 | Planlı doğrulandı |
|---|---|---|---|---|---|---|---|---|---|---|---|
| H1 | 2026-01-05 | 2026-02-20 | voluntary | 03 | permanent | — | 46 | – | ✔ | ✔ | false (C1 yok) |
| H2 | 2026-01-12 | 2026-03-31 | project_end | 18 | project | 2026-03-31 | 78 | – | ✔ | ✔ | **true** |
| H3 | 2026-02-02 | 2026-04-15 | contract_end | 05 | fixed_term | 2026-04-15 | 72 | – | ✔ | ✔ | **true** |
| H4 | 2026-01-20 | 2026-06-25 | voluntary | 03 | permanent | — | 156 | – | – | ✔ | false |
| H5 | 2026-02-16 | 2026-09-30 | project_end | 18 | project | 2026-09-30 | 226 | – | – | – | (kohort dışı süre) |
| H6–H10 | Ocak–Mart | — | — | — | — | — | — | – | – | – | — |

("Gün" = çıkış − giriş; erken ayrılış koşulu `çıkış < giriş + gün`.)

| # | Durum | KPI-021 (30 g) | KPI-018 (90 g) | KPI-022 (180 g) | Dışlanan pay (90 g) |
|---|---|---|---|---|---|
| **W1** | Bugün (v1) | 0/10 = **%0** (gerçek sıfır; kohort tam gözlenmiş) | 3/10 = **%30** (kırmızı ≥20) | 4/10 = **%40** (kırmızı ≥32) | — |
| **W2** | Kural sonrası (v2, `verified`) | **%0** | 1/10 = **%10** (H1) → `good` sınırı: uyarı 15'in altı | 2/10 = **%20** (H1, H4) → `good` (uyarı 25'in altı) | 2/10 = **%20** |
| **W3** | **Sınır — kod/kategori çelişkisi:** H3 kaynakta `contract_end` ama SGK **04**, sözleşme `permanent` | %0 | SGK kazanır → `employer_initiated` → 2/10 = **%20** | 3/10 = **%30** | 1/10 = %10; `category_code_conflicts = 1` |
| **W4** | **Sınır — planlanandan erken bitiş:** H2 planlanan bitiş 2026-06-30, çıkış 2026-03-31 (C4: 03-31 < 06-23) | %0 | `false` → sayılır → 2/10 = **%20** | 3/10 = **%30** | 1/10 = %10; uyarı `planned_end_mismatch` |
| **W5** | **Eksik veri:** H2'nin SGK kodu yok, sözleşme tipi boş (→ `unknown`), planlanan bitiş yok | %0 | C2 değerlendirilemez → `null` → sayılır → 2/10 = **%20**; sınıflandırma kalitesi = 100 × (1 − 1/3) = **66,7** | 3/10 = %30 | 1/10 = %10 |
| **W5-alt** | Aynı veri, seçenek `category` | %0 | 1/10 = %10 (yalnızca kategoriye güvenir) | 2/10 = %20 | 2/10 = %20 |
| **W6** | **Gözlenmemiş kohort:** kohort 2026-09-01–2026-09-30, gözlem 2026-11-05 | 30 g tam gözlenmiş | 90 g gözlenmemiş → değer + not, kalite 50 (mevcut) | aynı | — |

### 4.2 KPI-020 — bilinmeyen kategori (12 ay pencere, ortalama kişi 400, 100 ayrılış)

| # | Ayrılış dağılımı | KPI-001 | KPI-020 v1 | KPI-020 v2 |
|---|---|---|---|---|
| **V1** | gönüllü 30, işveren 20, sözleşme sonu 15, proje sonu 25, bilinmeyen 10 (u = %10) | 100/400 = **%25,0** | 30/400 = **%7,5**, `good`, kalite 90 | **%7,5**, renk yok, aralık **%7,5–%10,0** ((30+10)/400), kalite 90 |
| **V2** | **Sınır:** bilinmeyen 5 (gönüllü 35), u = %5 | %25,0 | 35/400 = **%8,75**, `good` | **%8,75**, `good` (u ≤ 0,05 dahil) |
| **V3** | **Eksik veri:** bilinmeyen 25 (gönüllü 15), u = %25 | %25,0 | 15/400 = **%3,75**, `good` (yanıltıcı), kalite 75 | **`insufficient_data`**, kalite 75 |
| **V4** | **Gerçek sıfır:** 0 ayrılış, istihdam beslemesi dönem için tam | **%0** | **%0**, kalite 100 | **%0**, `good` |
| **V5** | **Veri yok:** istihdam beslemesi dönemin son 2 ayı için yüklenmemiş (WP-6) | v1: düşük değer | v1: düşük değer | `insufficient_data` (kapsam kuralı, WP-6 §3.6) |
| **V6** | **Eşleme etkisi:** V1'deki 10 bilinmeyenin 8'i SGK 23–25 (işçinin haklı/zorunlu nedenle feshi), 2'si kodsuz | — | — | gönüllü 38 → 38/400 = **%9,5**; u = 2/100 = %2 → `good`, kalite 98 |

### 4.3 KPI-002 — 12 ay kuralı

| # | Pencere | Başlangıç nüfusu → kalan | v1 | v2 |
|---|---|---|---|---|
| **R1** | 2025-11-01–2026-10-31 (365 gün) | 200 → 128 | **%64,0**, `warning` (≤65) | **%64,0**, `warning` |
| **R2** | 2026-10-01–2026-10-31 (31 gün) | 200 → 194 | **%97,0**, `good` (sahte rahatlık) | **%97,0**, renk **yok** + not |
| **R3** | **Sınır:** 360 gün (2025-11-06–2026-10-31) / 359 gün (2025-11-07–2026-10-31) | 200 → 128 | renkli / renkli | 360 → renkli; 359 → renk yok |
| **R4** | **Veri yok:** yeni proje, dönem başında kapsamda kimse yok | 0 → 0 | `insufficient_data` (mevcut) | aynı |
| **R5** | **Gerçek sıfır:** proje tamamen demobilize | 20 → 0 | **%0**, `critical` | **%0**, `critical` (12 ay pencerede) |

## 5. Kabul kriterleri

1. W1–W6, V1–V6, R1–R5 değerleri birebir üretilir.
2. KPI-018/021/022 v2'de `planned_end_verified IS NOT TRUE` olan her erken ayrılış sayılır; `null` asla dışlanmaz.
3. Dışlanan pay her KPI-018/021/022 sonucunda `breakdown`'da yer alır; KPI değeriyle aynı bastırma kuralına tabidir.
4. Katalogda `exclude_categories` vb. bir hesaplayıcının okumadığı parametre **reddedilir** (beyaz liste testi).
5. SGK kodu ile kategori çelişirse kod kazanır ve uyarı üretilir; doğrulanmamış eşleme satırı kullanılmaz.
6. Sağlıkla ilgili kodlar (09, 24, 28) hiçbir tabloya yazılmaz; kategori yazılır.
7. Boş `contract_type` artık `permanent` olarak saklanmaz.
8. KPI-020 bantları uygulanır; renk bastırma `KpiResult` şeklini değiştirmez (`threshold_status = null`).
9. KPI-002 360 günden kısa pencerede renk üretmez; `annualized_value` KPI-002'de daima `null`.
10. v1 sonuçları korunur; KPI-001 değeri hiçbir kuraldan etkilenmez (yalnızca eşleme ile kategori kırılımı değişir).
11. Kişi düzeyi çıktı yok; tüm göstergeler grup düzeyi ve minimum grup boyutuna tabi.

## 6. Önerilen test adları (A12)

- `test_early_turnover_excludes_only_verified_planned_ends` (W1, W2)
- `test_early_turnover_sgk_code_overrides_conflicting_category` (W3)
- `test_planned_end_before_planned_date_counts_as_early_leaver` (W4)
- `test_unverifiable_planned_end_counted_and_lowers_quality` (W5)
- `test_planned_end_exclusion_category_mode` (W5-alt)
- `test_early_turnover_excluded_share_in_breakdown_and_suppressed_with_value`
- `test_unread_formula_parameter_rejected_per_calculator` (M2)
- `test_sgk_exit_code_mapping_table_drives_category` / `test_unverified_mapping_row_ignored`
- `test_health_related_exit_codes_not_stored`
- `test_blank_contract_type_stored_as_unknown_not_permanent`
- `test_transfer_code_16_without_new_spell_flagged`
- `test_voluntary_turnover_unknown_share_bands` (V1–V3)
- `test_voluntary_turnover_true_zero_vs_missing_feed` (V4, V5 — WP-6 ile)
- `test_retention_no_colour_below_360_days` (R2, R3)
- `test_retention_never_annualised`
- `test_turnover_formula_v1_results_preserved_after_v2`

## 7. Etkilenen sözleşmeler / ADR gerekli mi

**ADR gerekli — evet** (tek ADR'de toplanabilir):
- `INTEGRATION_CONTRACTS.md` §6 istihdam import alanları (`sgk_exit_code`, `planned_end_date`, `contract_type=unknown`).
- Şema: `employment_relationships` yeni kolonlar, `contract_type` değer kümesi, `termination_code_map` tablosu → yeni Alembic revizyonu.
- Formül versiyonları: KPI-018/021/022 (hesaplayıcı değişikliği + yeni parametre), KPI-020 (yeni parametre + motor
  renk bastırma işareti), KPI-002 (yalnızca `threshold_min_days`; hesaplayıcı değişmez).
- `engine.PARAM_RULES` → hesaplayıcı başına beyaz liste (ortak sözleşme).
- `KpiResult` şekli değişmez.

## 8. Teşvik ve manipülasyon riski

| Risk | Kontrol |
|---|---|
| İşveren feshini "proje sonu" kodlamak (en büyük risk) | SGK kodu kazanır; `permanent` + planlı bitiş reddedilir; planlanan bitişten > 7 gün önce bitiş sayılır; dışlanan pay ayrı izlenir ve "erken ayrılış ↓ + dışlanan pay ↑" alarmı |
| Kısa belirli süreli sözleşmeleri zincirleme kullanmak (her biri "planlı bitiş") | `repeated_short_fixed_term_persons` lineage'da; ⚖️ L-11 |
| Gönüllü ayrılışı "bilinmeyen" bırakmak (KPI-020'yi düşürmek) | v2 bantları: %5 üstünde renk yok, %20 üstünde değer yok — bilinmeyen bırakmak artık "iyi" görünmez |
| Ayrılışı kod 22 "diğer" ile bildirmek | Düşük bilgi payı uyarısı (> %15) |
| Kod 16 (nakil) ile ayrılışı gizlemek | Yeni dönem yoksa uyarı; KPI-001'de 7 gün kuralı zaten yeni dönem arar |
| 12 ay kuralını aşmak için kısa pencere raporlamak (KPI-002) | Kısa pencerede renk yok; yönetim raporları 12 ay kayan pencere kullanır |
| Deneme süresi içinde fesih (01) yerine 90. günden sonraya erteleme | KPI-022 (180 g) birlikte okunur (mevcut öneri) |

## 9. ⚖️ maddeler (Agent 20'ye soru — hukuki yorum değildir)

| # | Kapalı uçlu soru | Güvenli varsayılan |
|---|---|---|
| L-8 | Sağlık bilgisini ima eden SGK çıkış kodlarının (09 malulen emeklilik, 24 işçinin sağlık nedeniyle feshi, 28 işverenin sağlık nedeniyle feshi) ham değeri platformda **saklanabilir mi** (KVKK md. 6)? Kod 11 (iş kazası sonucu ölüm) aynı kapsamda mı? | 09/24/28 saklanmaz, yalnızca kategori; 11 kategori `death` + grup düzeyi İSG tutarlılık kontrolü, ham kod saklanır mı → yanıta kadar **saklanmaz** |
| L-9 | Kod 13 (kadın işçinin evlenmesi) gönüllü devire (KPI-020) dahil edilirse 4857 md. 5 / 6701 açısından dolaylı ayrımcılık riski doğar mı? | `other` (KPI-020 dışında) |
| L-10 | İnşaatta kod 17/18 ile biten **belirsiz süreli** sözleşmeler planlı bitiş sayılabilir mi, yoksa işveren feshi niteliğinde mi? (md. 11 "belirli bir işin tamamlanması") | Yalnızca `fixed_term/project/seasonal` sözleşmede planlı; `permanent`'ta sayılır |
| L-11 | Aynı işçiyle art arda belirli süreli sözleşme (md. 11 zincirleme) yapıldığında son "sözleşme sonu" planlı bitiş olarak dışlanabilir mi? | 12 ayda ≥ 2 planlı bitişi olan kişiler lineage'da sayılır; dışlama devam eder ama İK Direktörü kararıyla kapatılabilir (S-2) |
| L-12 | SGK çıkış kodu listesinin güncel hali (29 ↔ 42–50 alt kodları, 30–34 açıklamaları) | Eşleme tablosu doğrulanana kadar etkin değil; kod → `unknown` |

## 10. Açık sorular (İK Direktörü, Agent 17)

- **S-1:** Dışlama modu: `verified` (önerim, temkinli) mi `category` mi? `verified`, veri kalitesi düşük şantiyelerde
  erken ayrılışı yüksek gösterir (W5) — bu, veri sağlama teşvikidir ama ilk aylarda kırmızı artırır.
- **S-2:** Zincirleme kısa sözleşmeli kişilerin planlı bitişleri dışlanmaya devam etsin mi (L-11 yanıtına kadar)?
- **S-3:** Kod 13 ve kod 23–25 kategorileri: 23–25'i `voluntary` saymak KPI-020'yi yükseltir (V6). Alt kategori
  (`voluntary_just_cause`) ayrı izlensin mi? (Kod 25 ücretin ödenmemesini içerir — kontrol edilebilir ve önemli.)
- **S-4:** KPI-020 bantları 0,05 / 0,20 uygun mu? (0,05 sizin §3.1'deki eşiğinizdir.)
- **S-5:** Alarm eşiği (dışlanan payda 3 dönemde ≥ 10 puan artış) ve izlenecek kapsam (proje / işveren).
- **S-6:** KPI-001 için de planlı bitişsiz bir "kontrol edilebilir devir" göstergesi istenir mi? (Önerim: hayır;
  KPI-020 + kategori kırılımı yeterli.)

## 11. Statü

**Taslak — İK Direktörü incelemesi bekliyor.** (İK Direktörü incelemesi bölümü bu satırın altına eklenecek.)

---

## İK Direktörü incelemesi (2026-11-05)

**İnceleyen:** Agent 17 (İK Direktörü) · **Karar: ŞARTLI ONAY** · Ölçütler: `HR_TEAM_PLAN.md` §2.4 ve §4.2. Ek dosya `WP-5_sgk_exit_code_map.csv` bu incelemenin kapsamındadır (CSV'ye satır eklenmedi).

### A. El hesaplarının yeniden hesaplanması

| Örnek | Benim hesabım | Sonuç |
|---|---|---|
| Gün sayıları | H1 46, H2 78, H3 72, H4 156, H5 226 | ✔ |
| W1 | <30: 0/10; <90: H1, H2, H3 = %30 (≥ 20 `critical`); <180: + H4 = %40 (≥ 32 `critical`) | ✔ |
| W2 | KPI-018: H1 = %10 (< 15 `good`); KPI-022: H1, H4 = %20 (< 25 `good`); dışlanan 2/10 | ✔ — plan örneğiyle (%30 → %10, 2/10) aynı |
| W3 / W4 | 2/10 = %20 → eşik 20'ye **eşit = `critical`** (belgede renk yazılmamış; testte `critical` beklenmeli); KPI-022 %30 (`warning`) | ✔ (koşul 6) |
| W5 | 1 belirsiz / 3 v1 erken ayrılış → kalite 66,7 | ✔ |
| V1 / V3 / V6 | 30/400 = %7,5, aralık (30+10)/400 = %10,0; u = %25 > %20 → `insufficient_data`; 38/400 = %9,5, u = %2, kalite 98 | ✔ |
| R1 / R3 | 128/200 = %64 (≤ 65 `warning`); 2025-11-06–2026-10-31 = 360 gün, 2025-11-07 = 359 gün (motor `(pe − ps).days + 1`) | ✔ |
| Bileşik not | 0,97^(365/30) = e^(12,17 × ln 0,97) ≈ %69,0 | ✔ |

### B. Onay koşulları

1. **CSV ↔ belge çelişkisi:** CSV kod 22 notu "> %10 uyarı", belge §3.5 "(unknown + other) / tüm ayrılışlar > %15". **Karar: %15, (unknown + other) birlikte.** CSV notu buna göre düzeltilir.
2. **Kod listesi eksik:** CSV 01–34 ve 42–50'yi kapsıyor; e-Bildirge listesinde 35 ve sonrası kodlar da var (aralarında doğum nedeniyle işten ayrılmaya ilişkin bir kod bulunduğunu düşünüyorum — numarası ve metni teyit edilmeli). Doğrulama sırasında **tüm güncel kodlar** eklenir. Doğum/analıkla ilişkili ayrılış kodları **D-6 gereği KPI-020 gönüllü devire girmez** (`other`) ve `health_related` gibi hassas işaretlenir (ham kod saklanmaz). Doğrulanana kadar tablo etkin değil (L-12) — belgenin kendi kuralı, aynen kalır.
3. **Kod 11 tutarlılık kontrolü ↔ KVKK varsayılanı:** L-8 güvenli varsayılanı kod 11'in ham değerini saklamıyor; o halde `fatality_not_in_hse_feed` kontrolü sonradan gelen İSG beslemesiyle tekrar çalışamaz. Çözüm: import sırasında yalnızca **tüzel kişilik × ay düzeyinde sayı** (`work_accident_death_exits`) tutulur (kişi bağı yok); kontrol iki besleme de `complete` olduğunda bu sayıyla çalışır.
4. **Gönüllü alt kırılım** (S-3 kararı): yeni platform kategorisi açılmaz (`TERMINATION_CATEGORIES` ortak sözleşmesi değişmez); KPI-020 `breakdown.voluntary_just_cause` = kod 23/25 sayısı (kod 24 sağlık kodu olduğu için ayrı gösterilmez, yalnızca toplamda), `suppress_small_counts` ile.
5. ⚖️ L-8…L-12 WP-3 dosya no. 1'de **yok** (o dosya L-1…L-7'yi aldı) → hukuk soru dosyası no. 2'ye girer; **L-8 aciliyet A** (çıkış kodu import'u gerçek veriden önce).
6. Kabul testlerinde W3/W4 KPI-018 için `threshold_status = "critical"` (sınır: değer = kritik eşik) açıkça beklenir.
7. **İzleme notu (bu WP'yi bloklamaz):** R5 — planlı demobilizasyonda KPI-002 %0 `critical` gösterir. Matematik doğru, sinyal yanlış (inşaat gerçekliği). Sprint 2'de KPI-002 için de doğrulanmış planlı bitişlerin ayrı gösterilmesi spesifikasyonu (Agent 19) yazılır; o zamana kadar proje kapanış ayında not.

### C. Açık sorulara kararlarım

| # | Karar | Gerekçe |
|---|---|---|
| S-1 | **`verified`** | Planlı bitiş ancak kanıtla dışlanır; işveren feshinin "proje sonu" kodlanması en büyük risk. İlk aylardaki fazla kırmızı, veri sağlama teşvikidir ve not ile açıklanır |
| S-2 | **Evet, dışlama sürer**; `repeated_short_fixed_term_persons` lineage'da ve KPI-018 notunda sayı olarak görünür; L-11 yanıtı "hayır" ise bir sonraki formül versiyonunda kapatılır | Hukuk yanıtından önce kuralı değiştirmek kararsızlık yaratır; sayı görünür olduğu için gizleme yok |
| S-3 | **23–25 `voluntary` kalır + alt kırılım** (koşul 4); **13 → `other`** (L-9 güvenli varsayılanı) | İşçinin haklı nedenle feshi işçi başlatımlıdır ve işverenin kontrol alanındadır (ücretin ödenmemesi dahil) — KPI-020'de görünmesi doğru teşviktir. Kod 13'ü gönüllü devire katmak kadın çalışanlar üzerinden dolaylı ayrımcılık riski taşır |
| S-4 | **0,05 / 0,20 kabul** | %5 benim §3.1 eşiğim; %20 üstü değer yanıltıcı (V3) |
| S-5 | **Kabul:** 3 dönemde ≥ 10 puan artış; kapsam **proje ve tüzel kişilik (alt yüklenici dahil)** | Kodlama davranışı firma düzeyinde oluşur; proje düzeyi demobilizasyonu yakalar |
| S-6 | **Hayır** | KPI-020 + kategori kırılımı yeterli; yeni gösterge eklemek inceleme yükü |

### D. Çapraz tutarlılık

- "Veri yok ↔ gerçek sıfır" kuralı WP-6 veri §3.6 kapısına (`employment` beslemesi) bağlı — V5 tutarlı.
- `planned_end_exclusion` + "hesaplayıcı başına parametre beyaz listesi" WP-4 koşul 1 (`min_pattern_coverage`) ve WP-6 veri (`min_feed_coverage`, `required_feeds`) için de geçerli olur — tek ADR (ADR-d).
- KPI-020 renk bastırması WP-2 koşul 1'deki ortak `status_reason` mekanizmasıyla yapılır (`unknown_category_share`) — ayrı `threshold_suppressed` bayrağı açılmaz.

### E. Statü

**İK Direktörü onaylı (şartlı)** — koşullar 1–7. KPI-002 `threshold_min_days: 360` (yalnızca parametre) ilk dalgaya alınabilir; çıkış kodu eşlemesi doğrulanmadan etkinleşmez. ADR gerekli. İnsan girdisi: bordro/SGK uygulayıcısı + İK — güncel çıkış kodu listesinin satır satır doğrulanması (`verified_by`); hukuk — L-8…L-12.
