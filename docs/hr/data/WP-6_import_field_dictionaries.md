# WP-6 — Kalan Import Tipleri (Puantaj, Eğitim, Maliyet) ve Veri Kapsamı Meta Verisi

> **Statü: Taslak — İK Direktörü incelemesi bekliyor.** Yazan: Agent 19 (İK Veri ve Metrik Spesifikasyon Analisti).
> Kod, katalog veya migration değiştirmez. Örnek dosyalar ve sayılar **kurgusaldır** (tutarlar ölçek anlamı taşımaz);
> hiçbir değer şirket tabanı değildir. Hukuki yorum değildir (⚖️ → Agent 20).
> **İSG olay import alanları bu belgede yoktur** — sahibi Agent 18: `docs/hr/hse/WP-6_safety_import_fields.md`
> (Agent 18 belgesi bu belgeye plandaki adıyla `DATA-03_...` diye atıf yapar; aynı belgedir). Burada yalnızca İSG
> beslemesinin **kapsam meta verisi** ile ilişkisi tanımlanır.

| | |
|---|---|
| Tarih | 2026-11-05 |
| İş paketi | WP-6 (`docs/HR_TEAM_PLAN.md` §3). Plandaki çıktı adı `DATA-03_import_specs_timesheet_training_cost_hse.md`; Orchestrator talimatıyla bu adla yazıldı |
| Girdiler | B-07, B-20, `AUDIT_REPORT.md` #19, `HR_DIRECTOR_RECOMMENDATIONS.md` §6.1 ve Ek E.1/E.3, `services/imports.py` (`FIELDS`, `LEAVE_FIELDS`, `FIELD_SETS`, `_resolve_employment`, `_validate_leave`), `models/operations.py` (`Timesheet`, `TrainingRecord`, `PayrollCost`, `SafetyEvent`), `models/platform.py` (`DataSource`, `ImportBatch`, `DataQualityIssue`), `calculators.py` (`_hours`, `training_hours_per_employee`, `labor_cost_total`, `_safety_rate`, `absenteeism_rate`), `INTEGRATION_CONTRACTS.md` §5–§7, `docs/PERMISSION_MATRIX.md`; Agent 18 WP-2 (R3 "eksik veri" satırı bu belgenin §3.6'sına atıf yapar) |
| Uygulayan (onaydan sonra) | A08 (import tipleri), A05 (kapsam tablosu + kolonlar → migration), A09 (B-20 kapsam kapısı), A07 (`/imports` kapsam matrisi), A11 (maliyet önizleme yetkisi), A12 |

Bölüm numaraları, WP-2 (Agent 18) ve WP-4/WP-5'teki çapraz referanslar nedeniyle sabittir: §3.4 kapsam modeli,
§3.6 hesaplayıcı kuralı.

---

## 1. Kapsam ve ilgili backlog ID'leri

- **B-07** (P1, ◐): puantaj, maliyet, eğitim, İSG import tipleri (izin import'u Done).
- **B-20** (P1) / **denetim #19**: eksik besleme gerçek sıfır gibi görünüyor — veri kapsamı meta verisi.
- **Öncelik sırası (kabul kriteri):** (1) **puantaj** ve **İSG** (KPI-003/053/054/014A/B/043'ü besler; Agent 18 İSG
  alanlarını verir) → (2) **eğitim** (KPI-008) → (3) **maliyet** (KPI-019/036/010). **Kapsam meta verisi, ilk yeni
  import tipiyle birlikte** devreye girmelidir; aksi halde puantaj yüklenip izin/İSG yüklenmeyen ayda devamsızlık ve
  kaza oranları sıfır görünür.
- Kapsam dışı: aylık "puantaj cetveli" geniş formatı (gün sütunları + gün kodları) — sprint 2 (bkz. §3.2.6);
  bağlayıcılar (§7 `Connector`) yalnızca kapsam beyanı açısından ele alınır.

## 2. Mevcut davranış (kod/doküman referansıyla)

| # | Yer | Bugünkü davranış | B-20 açısından sonuç |
|---|---|---|---|
| M1 | `imports.FIELD_SETS` | Yalnızca `employment` ve `leave` | Puantaj/eğitim/maliyet yalnızca seed veya doğrudan DB ile girer |
| M2 | `absenteeism_rate` | `has_source_data = puantaj satırı > 0`; izin satırı yoksa pay = 0 | Puantaj var, izin beslemesi yoksa KPI-003/028/053 **%0** (yeşil) |
| M3 | `_safety_rate` | `has_source_data = puantaj satırı > 0`; olay yoksa pay = 0 | İSG beslemesi yoksa LTIFR/TRIR **0** (en tehlikeli yanlış sıfır) |
| M4 | `training_hours_per_employee` | `has_source_data = ort. kişi > 0`; eğitim kaydı yoksa 0 | Eğitim beslemesi yoksa KPI-008 **0** (kırmızı ama yanlış nedenle) |
| M5 | `labor_cost_total` | Hiç satır yoksa `insufficient_data`; **kısmi** satırda (bir firma eksik) toplam eksik, payda tam | KPI-019 sessizce düşük |
| M6 | `DataSource` | `status`, `last_success_at`, `last_error` var; **dönem × kapsam** bilgisi yok | "Ekim puantajı A firması için yüklendi mi?" sorusu yanıtlanamaz |
| M7 | `imports.upload` | Önizleme (`preview: rows[:20]`) `import.write` sahibine ham satırları döndürür | Maliyet import'unda ücret tutarları `cost.read` yetkisi olmayan import kullanıcısına görünür (bkz. §8, ⚖️ L-13) |
| M8 | `Timesheet` | Günlük satır (`uq_timesheet_day`); gün tipi, granülerlik alanı yok. Demo puantajı haftalık özet satırlarından oluşur (yalnızca yapı) | İzinli gün senaryosu (WP-4 §3.5) ve cumartesi tespiti yapılamaz |

## 3. Önerilen kural

### 3.1 Ortak import kuralları (tüm yeni tipler — mevcut `employment`/`leave` deseni)

| Konu | Kural |
|---|---|
| Akış | `POST /imports` (multipart: `file`, `entity_type ∈ {timesheet, training, cost}`, **yeni**: `coverage` beyanı §3.5) → `validate` → `commit` (idempotent, chunk'lı, `failed` → kaldığı yerden) |
| Alan seti | `FIELD_SETS["timesheet" \| "training" \| "cost"]`; her alan `required` + TR/EN/RU `aliases`; başlık normalizasyonu `_norm` |
| Kimlik eşleştirme | `_resolve_employment(source_system, external_id, legal_entity, tarih)`: kimlik haritası (`person_identifiers`) → yoksa firmadaki `employee_number`; tarih = `work_date` / `completed_on` / `period_month`'un son günü. Bulunamaz → hata `no_employment`; birden fazla → hata `ambiguous_employment` (izin import'uyla aynı) |
| Kapsam yetkisi | `ctx.entity_filter("import.write")` dışındaki firma → hata `scope` |
| Dönem kapalı | Satır tarihi kapalı bir ayda (`period_closes.status = closed`) → hata `period_closed` (D-2: yalnızca ana yüklenici açabilir) |
| Mükerrer | Dosya içi aynı doğal anahtar → hata `duplicate_in_file`. DB'de aynı anahtar → varsayılan **atla** + uyarı `already_imported`; batch `mode=replace` (yalnızca açık dönem) → değiştir + denetim kaydı |
| Veri kalite kuyruğu | Her hata/uyarı `data_quality_issues`'a (`rule_code`, `severity`, `row_number`, `record_ref = external_id`); `errors.csv` |
| Bayatlatma | Commit → `mark_stale_from(en erken tarih)` (`INTEGRATION_CONTRACTS.md` §5); puantajda özet tetikleyicileri ek olarak |
| Boş ≠ 0 | Sayısal alan boşsa **NULL**; yalnızca alan sözlüğünde açıkça "boş → 0" yazan alanlar 0 alır |
| Sayı biçimi | `,` ondalık ayırıcı kabul edilir (`"7,5"`); binlik ayırıcı (`1.234,50`) yalnızca maliyet tutarında tanınır |

### 3.2 Puantaj (`entity_type = timesheet`) — bir satır = bir çalışan × gün × proje

#### 3.2.1 Alan sözlüğü

| Alan | Zor. | Tip / birim | Takma adlar (TR / EN / RU) | Doğrulama → kod | Hedef |
|---|---|---|---|---|---|
| `external_id` | ✔ | metin | sicil, sicil no, personel no / employee id / табельный номер | boş → `required` | eşleme |
| `source_system` | | metin | kaynak / source / система | boşsa `import` | `source_system` |
| `legal_entity_code` | ✔ | metin | firma, firma kodu, şirket / company, employer / компания | yok → `unknown_legal_entity` | eşleme |
| `work_date` | ✔ | tarih | tarih, çalışma tarihi, gün / date, work date / дата | biçim → `date_format`; gelecek → `future_date` (hata); istihdam dışında → `no_employment` | `work_date` |
| `project_code` | | metin | proje, şantiye / project, site / проект | bilinmeyen → `unknown_project` (hata) | `project_id` |
| `shift_code` | | metin | vardiya / shift / смена | bilinmeyen → uyarı `unknown_shift` | `shift_id` |
| `planned_hours` | ✔ | saat, 2 ondalık | planlı saat, normal saat / planned hours / плановые часы | 0–24 → `hours_range`; boş → hata (planlı 0 yalnızca hafta tatili/tatil/izin günü için açıkça 0 yazılır) | `planned_hours` |
| `worked_hours` | ✔ | saat (fazla çalışma **dahil**) | çalışılan saat, fiili saat / worked hours / отработано | 0–24; `worked > 11` → uyarı `daily_max_exceeded` ⚖️ md. 63 | `worked_hours` |
| `overtime_hours` | | saat | fazla mesai, fazla çalışma / overtime / сверхурочные | `≤ worked` → aksi hata `overtime_exceeds_worked`; boş → **0** (fazla çalışma yoksa kaynaklar boş bırakır) | `overtime_hours` |
| `night_hours` | | saat | gece saati / night hours / ночные часы | `≤ worked` → aksi hata `night_exceeds_worked`; `> 7,5` → uyarı `night_over_7_5` ⚖️ md. 69; boş → **0** | `night_hours` |
| `productive_hours` | | saat | üretken saat, iş paketi saati / productive hours / производительные часы | `≤ worked` → aksi hata; **boş → NULL** (bilinmiyor; KPI-004 `min_coverage` kuralı) | `productive_hours` |
| `day_type` | | enum `work` \| `weekly_rest` \| `public_holiday` \| `leave` \| `unknown` | gün tipi / day type / тип дня | bilinmeyen değer → `unknown` + uyarı | **yeni kolon** |
| `status` | | enum `approved` \| `draft` | onay, durum / status / статус | Kolon eşlenmemişse batch beyanı `approved_source = true` zorunlu; yoksa satırlar `draft` (KPI'ya girmez) + uyarı `approval_unknown` | `status` |
| `period_days` | | tam sayı ≥ 1 | dönem gün / period days | > 1 → satır dönem toplamıdır; uyarı `non_daily_granularity` (WP-4 tespitleri yapılamaz) | **yeni kolon** (varsayılan 1) |

**Doğal anahtar:** `(employment_id, work_date, project_id)` (= mevcut `uq_timesheet_day`).

#### 3.2.2 Satırlar arası kontroller (validate sonunda, grup düzeyi sayım)

| Kod | Kural | Ciddiyet |
|---|---|---|
| `overtime_weekly_inconsistent` | ISO hafta başına Σ`overtime` ile `max(0, Σworked − haftalık saat)` farkı > 1 sa (haftalık saat = WP-4 düzeni, yoksa 45). Fazla çalışma **haftalık** tanımlıdır ⚖️ md. 41, denkleştirme L-15 | uyarı |
| `leave_and_work_same_day` | Aynı gün yıllık/raporlu izin kaydı + `worked_hours > 0` (E.1 "kâğıt üstünde izin" kontrolü). Panoda yalnızca sayı; düzeltme listesi İK Operasyon yetkisiyle | uyarı |
| `planned_on_leave_mismatch` | WP-4 §3.5 otomatik senaryo tespiti ≠ kapsam beyanı | uyarı |
| `pattern_timesheet_mismatch` | WP-4 §8 (5 günlük düzende cumartesi planlı saat vb.) | uyarı |

#### 3.2.3 Örnek dosya (kurgusal; `;` ayraçlı)

```csv
sicil;firma;tarih;proje;planlı saat;çalışılan saat;fazla mesai;gece saati;üretken saat;gün tipi
P-0001;ANA-01;2026-10-05;PRJ-A;7,5;10;2,5;0;8;work
P-0001;ANA-01;2026-10-11;PRJ-A;0;0;0;0;;weekly_rest
P-0002;ANA-01;2026-10-05;PRJ-A;7,5;6;0;8;;work
```
Satır 4 (P-0002) → **hata** `night_exceeds_worked` (8 > 6). Satır 3 → geçerli; `productive_hours = NULL`.

#### 3.2.4 Kimlik ve izin ilişkisi
Puantajdaki `day_type = leave` satırı **izin kaydı oluşturmaz** (izin yalnızca izin beslemesinden; çift sayım önlenir);
yalnızca WP-4 Senaryo A kanıtıdır.

#### 3.2.5 "Veri yok" ↔ "gerçek sıfır"
Puantaj satırı olmayan çalışan-gün, beslemenin kapsamı **tam** ise "o gün planlı saati yok" demektir; kapsam yoksa
bilinmez → §3.6.

#### 3.2.6 Sprint 2: puantaj cetveli (geniş format)
Logo/Netsis tipi aylık cetvel (gün sütunları, gün kodları: çalıştı / yıllık izin / rapor / hafta tatili / genel tatil /
ücretsiz / devamsız) hem puantaj hem izin üretir. Ayrı spesifikasyon gerekir: gün kodu → izin türü eşlemesi
**`normalize_leave_type` üzerinden** (D-6) ve aynı ay için ayrıca izin dosyası yüklenirse çift sayım kuralı.

### 3.3 Eğitim (`entity_type = training`) — bir satır = bir çalışanın tamamladığı bir eğitim

#### 3.3.1 Alan sözlüğü

| Alan | Zor. | Tip / birim | Takma adlar (TR / EN / RU) | Doğrulama → kod | Hedef |
|---|---|---|---|---|---|
| `external_id` | ✔ | metin | sicil, personel no / employee id / табельный номер | | eşleme |
| `source_system` | | metin | kaynak / source (LMS) | | |
| `legal_entity_code` | ✔ | metin | firma / company / компания | | eşleme |
| `course_code` | ✔ | metin | eğitim kodu, ders kodu / course code / код курса | boş → `required` | `course_code` |
| `course_name` | ✔ | metin | eğitim adı / course name / название курса | | `course_name` |
| `category` | ✔ | enum `hse` \| `technical` \| `compliance` \| `soft` | eğitim türü, kategori / category / категория. Değer eşanlamlıları: "isg", "iş güvenliği", "ohs", "hse" → `hse`; "teknik", "mesleki" → `technical`; "uyum", "yasal" → `compliance`; "kişisel gelişim" → `soft` | eşlenemeyen → **hata** `unknown_training_category` (varsayılan kategori atanmaz) | `category` |
| `legal_basis` | | enum (aşağıda) / boş | yasal dayanak / legal basis | bilinmeyen → uyarı, NULL | **yeni kolon** |
| `hours` | ✔ | saat, 2 ondalık | süre, saat / hours / часы | `> 0`; `start_date` varsa `≤ 24 × gün`; yoksa `> 24` → uyarı `hours_exceed_single_day`; `≤ 0` → hata `hours_range` | `hours` |
| `completed_on` | ✔ | tarih | tamamlanma tarihi, eğitim tarihi / completion date / дата окончания | gelecek → hata; istihdam dışı → `no_employment` | `completed_on` |
| `start_date` | | tarih | başlangıç / start / начало | `≤ completed_on` | yeni kolon (ops.) |
| `status` | | enum `completed` \| `attended` \| `failed` \| `cancelled` | durum / status / статус | Yalnızca `completed` sayılır; kolon eşlenmemişse batch beyanı `all_completed = true` zorunlu (varsayılan atanmaz) | filtre |
| `delivery` | | enum `classroom` \| `online` \| `on_the_job` | yöntem / delivery | ⚖️ L-14 (uzaktan İSG eğitimi) | yeni kolon (ops.) |
| `mandatory` | | bool | zorunlu / mandatory | boş → `false` (mevcut model varsayılanı; şirket zorunluluğu) | `mandatory` |

`legal_basis` değerleri (öneri; içerik ve süreler **Agent 18 ve Agent 20 teyidinde** ⚖️ L-14):
`ISG_TEMEL` (temel İSG eğitimi), `ISG_ISE_BASLAMA` (işe başlama / bilgilendirme), `ISG_YENILEME` (periyodik tekrar),
`ISG_GOREV_DEGISIKLIGI` (görev/ekipman değişikliği), `ISG_KAZA_SONRASI` (iş kazası/meslek hastalığı sonrası),
`ISG_UZUN_AYRILIK` (uzun süre işten uzak kalma sonrası), `MYK_BELGE`, `OPERATOR_BELGE`, `YUKSEKTE_CALISMA`, `DIGER_YASAL`.
Türetilen: **`is_statutory_hse = (category = hse AND legal_basis LIKE 'ISG_%')`**.

**Doğal anahtar:** `(employment_id, course_code, completed_on)`.

#### 3.3.2 İSG / mesleki ayrımı ve KPI-008

- KPI-008 değeri (toplam saat / ortalama kişi) **değişmez**. Kırılım eklenir: `hours_by_category` ve
  `statutory_hse_hours_per_employee` (yıllıklaştırılmış).
- KPI-008 kritik eşiği 16 = çok tehlikeli sınıf yasal İSG eğitimi asgarisi (HR Director §3.6). Yasal asgari
  **işyeri tehlike sınıfına** bağlıdır (az / tehlikeli / çok tehlikeli ⚖️); bu alan (proje veya işveren düzeyinde
  `hazard_class`) Agent 18'in alanıdır — burada yalnızca referans. `statutory_hse_hours_per_employee` (yıllık) < sınıf
  asgarisi → not: "grup ortalamasında yasal İSG eğitim asgarisi karşılanmıyor olabilir; kişi düzeyi uyumu göstermez".
- Kırılım kişi değil grup düzeyidir; kategori sayıları `suppress_small_counts` kuralına tabidir.

#### 3.3.3 Örnek dosya (kurgusal)

```csv
sicil;firma;eğitim kodu;eğitim adı;kategori;yasal dayanak;saat;tamamlanma tarihi;durum
P-0001;ANA-01;ISG-TEMEL-16;Temel İSG eğitimi;İSG;ISG_TEMEL;16;2026-10-07;completed
P-0002;ALT-07;OP-VINC-01;Mobil vinç operatör tazeleme;mesleki;OPERATOR_BELGE;8;2026-10-08;completed
P-0003;ANA-01;ISG-YEN-08;İSG yenileme;İSG;ISG_YENILEME;-4;2026-10-09;completed
```
Satır 4 → **hata** `hours_range` (−4). Satır 2: `category = hse`, `is_statutory_hse = true`.

### 3.4 Veri kapsamı meta verisi — model (B-20, A05)

**Tablo `data_feed_coverage`** — bir satır = bir besleme tipi × işveren × (ops.) proje × ay için beyan.

| Alan | Tip | Açıklama |
|---|---|---|
| `tenant_id` | FK | |
| `feed_type` | enum `employment` \| `leave` \| `timesheet` \| `training` \| `cost` \| `safety` \| `output` \| `downtime` | |
| `legal_entity_id` | FK | Zorunlu (alt yüklenici verisi işverenine göre gelir) |
| `project_id` | FK, null | null = işverenin tüm projeleri |
| `period_month` | date (ayın 1'i) | Kapsam ayı |
| `completeness` | enum `complete` \| `partial` \| `none` | `none` = "bu ay için besleme yok" açık beyanı. **Satır yoksa durum `undeclared`'dır ve `none` gibi davranır** |
| `coverage_ratio` | numeric(4,3), null | Yalnızca `partial` için (ör. 0,6 = kişilerin/şantiyelerin %60'ı) |
| `confirmed_empty` | bool | `complete` ve bu kapsam-ay için **0 satır** → true ("kayıt yok, onaylı" — gerçek sıfır kanıtı) |
| `planned_on_leave_days` | enum `included` \| `excluded` \| `unknown` | Yalnızca `timesheet` (WP-4 §3.5) |
| `attributes` | JSON, null | Beslemeye özgü kapsam öznitelikleri. `safety` için Agent 18 WP-6 §3.6: `includes_subcontractor_cases`, `includes_near_miss` (hayır → KPI-043 o kapsamda `insufficient_data`) |
| `basis` | enum `import_batch` \| `connector_run` \| `manual_declaration` | |
| `import_batch_id` / `data_source_id` | FK, null | İz |
| `declared_by`, `declared_at`, `note` | | Denetim izi; ör. "Ekim puantajı henüz kapanmadı" |
| Benzersizlik | `(tenant, feed_type, legal_entity_id, project_id, period_month)` | Sonraki beyan öncekini değiştirir (geçmiş `audit_logs`'ta) |

### 3.5 Kapsam beyanı nasıl oluşur

1. **Import ile (birincil yol):** upload isteği isteğe bağlı `coverage` nesnesi taşır:
   ```json
   {"period_start": "2026-10-01", "period_end": "2026-10-31", "legal_entity_codes": ["ANA-01", "ALT-07"],
    "project_codes": null, "completeness": "complete", "planned_on_leave_days": "included"}
   ```
   Commit başarıyla bitince her (firma × ay) için satır yazılır. Beyan edilen dönem/firma dışındaki satırlar yine
   yüklenir ama uyarı `row_outside_declared_coverage` alır ve kapsam oluşturmaz. Beyan yoksa: satırı olan her
   (firma × ay) için `partial` (oran bilinmiyor) yazılır — **satır varlığı tamlık kanıtı değildir**.
2. **Boş besleme:** olay tipi beslemeler (`leave`, `training`, `safety`) için **yalnızca başlık satırı olan dosya**
   kabul edilir (`completeness = complete` zorunlu) → `confirmed_empty = true`. `safety` için boş beyan yetkisi İSG
   rolündedir (öneri; A11 — Agent 18 belgesine bakınız) ve her zaman denetim kaydı yazar.
3. **Bağlayıcı:** `Connector.fetch(since)` başarılı ve bağlayıcı tamlık semantiği destekliyorsa `complete`; değilse
   `partial`. Erişilemezlik (`last_error`) kapsam yazmaz.
4. **Manuel beyan:** `/imports` ekranında kapsam matrisi (besleme × ay × firma; durumlar: tam / kısmi / beyan yok /
   kayıt yok-onaylı / besleme yok). Yalnızca o firmada `import.write` sahibi; `none` ve `confirmed_empty` beyanları
   gerekçe notu zorunlu.
5. **Dönem kapatma (D-2):** kapatılan ayda zorunlu beslemelerden (§3.6 tablo) kapsamı `complete` olmayan varsa kapatan
   kullanıcıya liste gösterilir ve kapanış kaydına yazılır (engelleyici olup olmayacağı açık soru S-3).
6. Her kapsam değişikliği `mark_stale_from(period_month)` çağırır (önbellek ve kalıcı sonuçlar bayatlar).

### 3.6 Hesaplayıcılar kapsamı nasıl kullanır (B-20 kuralı, A09)

**KPI → zorunlu beslemeler** (katalog parametresi `required_feeds`; parametre değişikliği → yeni formül versiyonu):

| KPI | Zorunlu beslemeler |
|---|---|
| KPI-001, 002, 016, 017, 018, 020, 021, 022, 023 | `employment` |
| KPI-003, 028, 053, 054 | `timesheet` + `leave` |
| KPI-055, 056 | `employment` + `leave` |
| KPI-004, 007, 032 | `timesheet` |
| KPI-014A, 014B, 043 | `timesheet` + `safety` (Agent 18 WP-2 R3 ile aynı) |
| KPI-008 | `employment` + `training` |
| KPI-019, 036, 010 | `cost` (+ `employment` / `timesheet` / `output` paydaya göre) |

**Kapsam oranı** (pencere × kapsam): `C = Σ kapsanan kişi-ay / Σ gerekli kişi-ay`. Gerekli kişi-ay = kapsamdaki her
(firma, ay) için o firmanın ay ortalama kişi sayısı (istihdam beslemesinden); kapsanan = `complete` → tam ağırlık,
`partial` → ağırlık × `coverage_ratio` (oran yoksa 0,5), `none`/`undeclared` → 0. Birden çok besleme zorunluysa
`C = min(C_besleme)`. `employment` beslemesinin kendisi için ağırlık firma-ay (eşit ağırlık).

**Kapı:**

| Durum | Sonuç |
|---|---|
| `C = 1` | Bugünkü hesap; `quality_score` hesaplayıcının kendi kuralı |
| `min_feed_coverage ≤ C < 1` | **Pay ve payda yalnızca kapsanan (firma × ay) üzerinden** hesaplanır (yanlılığı önler); `quality_score ≤ C × 100`; not: "N firma-ay kapsam dışı" |
| `C < min_feed_coverage` | **`insufficient_data`**, `value = null`, `quality_score = C × 100` |
| Kapsanan bölümde hiç kayıt yok ve kapsam `complete` | **Gerçek sıfır** (`value = 0`, normal renk kuralı; İSG'de WP-2 R1 maruziyet kuralı ayrıca uygulanır) |

Parametre `min_feed_coverage` (öneri **0,8**; `safety` için **0,9** — Agent 18 kararıyla). Mevcut `min_coverage`
anahtarı KPI-004'te başka anlamda (üretken saat kapsamı) kullanıldığı için **yeni anahtar**.
`lineage.coverage = {feed: C}` ve kapsam dışı firma-ay sayısı (kişi bilgisi yok). `KpiResult` şekli değişmez.

**Geçiş modu** (tenant ayarı `coverage_enforcement`): `off` (bugün) → `warn` (beyan yoksa bugünkü hesap, kalite ≤ 50,
not "kapsam beyan edilmemiş") → `enforce`. Yeni tenant'lar `enforce`; mevcut tenant'lar `warn` ile başlar, tüm
beslemeler için 3 ay kapsam beyanı birikince `enforce`'a geçer (öneri; S-2).

### 3.7 Maliyet (`entity_type = cost`) — bir satır = bir çalışan × ay × maliyet kalemi (× proje)

#### 3.7.1 Alan sözlüğü

| Alan | Zor. | Tip / birim | Takma adlar (TR / EN / RU) | Doğrulama → kod | Hedef |
|---|---|---|---|---|---|
| `external_id` | ✔ | metin | sicil / employee id / табельный номер | | eşleme |
| `source_system` | | metin | kaynak / source (bordro) | | |
| `legal_entity_code` | ✔ | metin | firma / company / компания | | eşleme |
| `period_month` | ✔ | `YYYY-MM` veya tarih | dönem, bordro dönemi, ay / period, payroll month / период | ayın 1'ine normalize; gelecek → hata | `period_month` |
| `cost_type` | ✔ | enum | kalem, ödeme türü / cost type / вид начисления. Değer eşanlamlıları: "brüt ücret" → `gross_wage`; "SGK işveren payı", "işveren primi" → `employer_ss`; "fazla mesai" → `overtime`; "yemek" → `meal`; "yol", "servis" → `transport`; "konaklama", "kamp" → `accommodation`; "yan hak" → `benefit` | eşlenemeyen → **hata** `unknown_cost_type` | `cost_type` |
| `amount` | ✔ | ondalık, 2 hane | tutar / amount / сумма | Negatif yalnızca `is_correction = true` ile; aksi hata `negative_amount` | `amount` |
| `currency` | ✔ | ISO 4217 | para birimi, döviz / currency / валюта | "TL" → `TRY` + uyarı; bilinmeyen → hata `currency_code`; ay sonundan önceki 45 günde kur yok → uyarı `fx_missing` (KPI `insufficient_data` olur — mevcut kural) | `currency` |
| `project_code` | | metin | proje / project / проект | bilinmeyen → hata | `project_id` |
| `cost_center_code` | | metin | masraf yeri / cost center / МВЗ | bilinmeyen → uyarı | `cost_center_id` |
| `is_correction` | | bool | düzeltme / correction | | yeni kolon (ops.) |

**Doğal anahtar:** `(employment_id, period_month, cost_type, project_id)`; düzeltme satırları anahtardan muaf (ayrı satır).

**Önerilen yeni kalemler** (enum değişikliği → ADR): `unused_leave_pay` (md. 59 fesihte izin ücreti) ve `severance`
(kıdem/ihbar). Gerekçe: Ek E.3 #9 "bordroda izin ücreti ödemesi var / fesih yok" istisna kontrolü bu kalem olmadan
yapılamaz. Kontrol (grup düzeyi sayı): `unused_leave_pay` olan çalışan-ay için o ay veya önceki ay `end_date` yoksa →
uyarı `leave_pay_without_termination` ⚖️.

**Yetki (A11):** maliyet import'unun önizlemesi ve `errors.csv` tutar içerir → yalnızca `cost.read` de taşıyan
kullanıcıya tutar gösterilir; diğerlerine maskeli önizleme (öneri; ⚖️ L-13). Şantiye düzeyi toplu (kişiye
bölünmemiş) maliyetler v1 kapsamı dışıdır (S-4).

#### 3.7.2 Örnek dosya (kurgusal; tutarlar anlamsız yer tutuculardır)

```csv
sicil;firma;dönem;kalem;tutar;para birimi;proje
P-0001;ANA-01;2026-10;brüt ücret;10000,00;TRY;PRJ-A
P-0001;ANA-01;2026-10;SGK işveren payı;2000,00;TRY;PRJ-A
P-0004;ALT-07;2026-10;fazla mesai;-500,00;TL;PRJ-B
```
Satır 4 → **hata** `negative_amount` (−500, `düzeltme` işareti yok) ve **uyarı** (`TL` → `TRY` dönüştürüldü).
Satır 2–3 geçerli.

### 3.8 İSG olayları (`entity_type = safety`) — sahibi Agent 18

Alan sözlüğü, vaka anahtarı, `worker_category`, `reported_date` ve sınıflandırma kuralları:
**`docs/hr/hse/WP-6_safety_import_fields.md` (Agent 18)**. Bu belge yalnızca şunları bağlar:
- Import tipi adı Agent 18 belgesindeki gibi `entity_type = safety_event`; kapsam tablosunda `feed_type = safety`.
- Agent 18'in batch kapsam beyanı (`coverage_from`, `coverage_to`, `legal_entity_code`, `project_code`,
  `includes_subcontractor_cases`, `includes_near_miss`) §3.5'teki `coverage` nesnesine eşlenir (`coverage_from/to` =
  `period_start/end`; son iki alan `attributes`'a). Tek ad kümesi seçimi İK Direktörünün terim sözlüğü kararıdır (S-7).
- `feed_type = safety` kapsam satırları §3.4–3.5'e göre; boş ay yalnızca `confirmed_empty` beyanıyla gerçek sıfırdır.
- KPI-014A/B/043 §3.6 kapısına tabidir (WP-2 R3 "eksik veri" satırı ile aynı kural).
- Ortak kurallar §3.1 (dönem kapalı, mükerrer, kuyruk) geçerlidir; kimlik eşleme yoktur (`SafetyEvent` kişi taşımaz).
- WP-5 kod 11 tutarlılık kontrolü (`fatality_not_in_hse_feed`) bu beslemenin kapsamı `complete` olan aylarda çalışır.

## 4. El hesaplı örnekler (tablo)

Tümü kurgusal. Firma A ortalama 100 kişi, firma B 50 kişi, aylar boyunca sabit; `min_feed_coverage = 0,8`.

| # | KPI / durum | Girdi | Bugün | Kural sonrası |
|---|---|---|---|---|
| **K1** | KPI-008, 2026-Q3 (92 gün), **gerçek sıfır** | A: 1.200 sa, `complete`; B: 0 satır, `complete` + `confirmed_empty` | 1.200 / 150 = **8,0 sa** (yıllık 8,0×365/92 = **31,74**) | Aynı: **8,0** / **31,74**, C = 1, kalite 100 |
| **K2** | KPI-008, **veri yok** | A: 1.200 sa `complete`; B beyan yok | **8,0** / 31,74 (B sıfır varsayılıyor) | C = 300 / 450 = **0,667** < 0,8 → **`insufficient_data`**, kalite 66,7 |
| **K3** | KPI-008, **sınır — kısmi kapsam** | A: 1.200 sa (3 ay `complete`); B: Tem+Ağu `complete` 90 sa, Eyl beyan yok | 1.290 / 150 = 8,6 | C = 400 / 450 = **0,889** ≥ 0,8 → yalnız kapsanan: pay 1.290; payda = 400 kişi-ay / 3 = 133,33 → **9,675 sa** (yıllık **38,38**), kalite 88,9, not "1 firma-ay kapsam dışı" |
| **K4** | KPI-014A, proje P, 2026-10, 90.000 çalışılan saat (puantaj `complete`) | İSG `complete` + `confirmed_empty` | **0,0** | **0,0** gerçek sıfır; renk yok (WP-2 R1: < 1.000.000 sa, "düşük maruziyet") |
| **K5** | KPI-014A, aynı proje | İSG beslemesi beyan yok | **0,0** (yanlış "sıfır kaza") | **`insufficient_data`** |
| **K6** | KPI-003 / 053, 100 kişi, planlı 18.000 sa (`complete`, `planned_on_leave_days = included`) | İzin beyan yok | **%0 / %0** (yeşil) | **`insufficient_data`** |
| **K7** | Aynı | İzin `complete`: 900 sa (300 sa habersiz + raporlu) | %5,0 / %1,67 | **%5,0 / %1,67** (değişmez) |
| **K8** | KPI-019, 2026-10 (kurgusal tutarlar) | A maliyet `complete` 5.000.000 TRY; B beyan yok | 5.000.000 / 150 = **33.333** (sessizce düşük) | C = 100/150 = 0,667 → **`insufficient_data`**. B `complete` 2.000.000 olsaydı: 7.000.000 / 150 = **46.667** |
| **K9** | Geçiş modu `warn`, hiç beyan yok | K2 girdisi | 8,0 | **8,0**, kalite ≤ **50**, not "kapsam beyan edilmemiş" |

## 5. Kabul kriterleri

1. Üç yeni tip `FIELD_SETS` deseninde; zorunlu alanlar, TR/EN/RU takma adlar, doğrulama kodları ve doğal anahtarlar bu
   belgedeki tablolarla birebir; her tip için örnek dosyadaki hatalı satır beklenen kodla reddedilir.
2. Boş sayısal alan yalnızca sözlükte "boş → 0" yazan alanlarda (`overtime_hours`, `night_hours`) 0 olur; diğerleri
   NULL veya hata. Kategori/kalem/durum için **varsayılan değer atanmaz**.
3. `data_feed_coverage` tablosu ve upload `coverage` beyanı; başlık-satırı-yalnız dosya `confirmed_empty` üretir.
4. K1–K9 değerleri birebir üretilir; "veri yok" hiçbir KPI'da `0` olarak dönmez (`INTEGRATION_CONTRACTS.md` §3
   değişmez 1: `status != ok ⇒ value == null`).
5. Kısmi kapsamda pay ve payda aynı (firma × ay) alt kümesinden hesaplanır.
6. Kapsam değişikliği ve her commit `mark_stale_from` çağırır.
7. Maliyet tutarları `cost.read` olmayan kullanıcıya önizlemede/hata dosyasında görünmez.
8. Eğitimde `is_statutory_hse` türetilir; KPI-008 kırılımı grup düzeyinde ve bastırmalıdır.
9. Kişi düzeyi rapor/sıralama yok; satır düzeyi hata listeleri yalnızca İK Operasyon / import yetkisiyle (bugünkü gibi).
10. D-6: hiçbir yeni import tipi izin türü üretmez; puantaj `day_type = leave` izin kaydı oluşturmaz.

## 6. Önerilen test adları (A12)

- `test_timesheet_import_field_set_and_aliases`
- `test_timesheet_night_exceeding_worked_rejected` (örnek satır 4)
- `test_timesheet_blank_productive_hours_stored_null_not_zero`
- `test_timesheet_weekly_overtime_consistency_warning`
- `test_timesheet_leave_day_row_does_not_create_leave_record`
- `test_timesheet_closed_period_rejected`
- `test_training_import_unknown_category_rejected_no_default`
- `test_training_negative_hours_rejected` (örnek satır 4)
- `test_training_statutory_hse_flag_and_kpi008_breakdown`
- `test_cost_import_currency_alias_and_negative_amount`
- `test_cost_import_preview_masks_amounts_without_cost_read`
- `test_coverage_declared_on_commit_per_entity_month`
- `test_header_only_file_confirms_empty_feed`
- `test_training_true_zero_vs_missing_feed` (K1, K2)
- `test_partial_coverage_restricts_numerator_and_denominator` (K3)
- `test_safety_rate_missing_feed_insufficient_not_zero` (K4, K5)
- `test_absenteeism_missing_leave_feed_insufficient` (K6, K7)
- `test_labour_cost_partial_entities_insufficient` (K8)
- `test_coverage_enforcement_warn_mode_caps_quality` (K9)
- `test_coverage_change_marks_results_stale`

## 7. Etkilenen sözleşmeler / ADR gerekli mi

**ADR gerekli — evet** (B-20 kapsam kapısı tüm KPI'ların sonucunu değiştirir):
- `INTEGRATION_CONTRACTS.md` §6: yeni `entity_type` değerleri, upload `coverage` nesnesi, `mode=replace`; §5: kapsam
  değişikliğinin `mark_stale_from` çağırması; §7: bağlayıcının kapsam yazması.
- Şema: `data_feed_coverage` tablosu; `timesheets.day_type`, `timesheets.period_days`; `training_records.legal_basis`,
  `start_date`, `delivery`; `payroll_costs.is_correction`; `cost_type` değer kümesine `unused_leave_pay`, `severance` →
  yeni Alembic revizyonu.
- Formül versiyonları: `required_feeds` + `min_feed_coverage` parametresi alan **her** KPI için yeni versiyon (v1
  sonuçları korunur). `PARAM_RULES`'a yeni anahtarlar.
- `KpiResult` şekli değişmez (`lineage.coverage`). Özet tablo yolu (B-21) kapsam birleşimini ay granülerliğinde
  desteklemeli — performans duman testi (A13 `perf_smoke.py`).
- A11: maliyet önizleme maskesi; `safety` boş beyan yetkisi.

## 8. Teşvik ve manipülasyon riski

| Risk | Kontrol |
|---|---|
| Olay olmayan ay için **yanlış "kayıt yok" beyanı** (özellikle İSG — kaza gizleme) | `confirmed_empty` yalnızca yetkili rol + zorunlu not + denetim kaydı; Agent 18 WP-2 S2 "bildirim sessizliği" sinyali; WP-5 kod 11 ↔ İSG tutarlılık kontrolü |
| Kötü sonucu gizlemek için kapsamı `partial`/`none` beyan etmek | `insufficient_data` panoda "veri eksik" olarak görünür ve İK veri kalitesi listesine düşer; kapsam oranı trendi izlenir; dönem kapatmada eksik kapsam listesi |
| Yalnızca "iyi" firmaların verisini yüklemek | Kapsam kişi-ay ağırlıklı; eksik firma oranı düşürür ve notta görünür |
| Puantajda üretken saati şişirmek (KPI-004) | Mevcut KPI-034 birlikte okuma önerisi; `productive_hours ≤ worked_hours` |
| Fazla çalışmayı gece/normal saate kaydırmak | Haftalık tutarlılık uyarısı; gece > 7,5 sa uyarısı (grup sayımı) |
| Eğitim saatini şişirmek / İSG dışı eğitimi İSG kodlamak | `legal_basis` + `category` tutarlılığı; tek günde > 24 sa uyarısı; kırılım İSG/mesleki ayrı |
| İzin ücretini fesih dışında ödeyip izin kaydını boş bırakmak | `leave_pay_without_termination` uyarısı (E.3) |

## 9. ⚖️ maddeler (Agent 20'ye soru — hukuki yorum değildir)

| # | Kapalı uçlu soru | Güvenli varsayılan |
|---|---|---|
| L-13 | Maliyet import'unda kişi bazlı ücret tutarlarının `cost.read` yetkisi olmayan import kullanıcısına önizlemede gösterilmesi KVKK md. 4 (ölçülülük) / md. 12 (güvenlik) açısından kabul edilebilir mi? Ham satırların `import_staging_rows`'ta saklama süresi ne olmalı? | Maskele; ham maliyet satırları commit'ten sonra B-24 saklama politikası kararına kadar en kısa süre (öneri 30 gün) tutulur |
| L-14 | İSG eğitim süreleri ve tekrar sıklığı tehlike sınıfına göre (8/12/16 sa; 1/2/3 yıl) güncel yönetmelikte bu şekilde mi? Uzaktan (online) eğitim hangi bölümler için geçerli sayılır? | Agent 18 teyidine kadar `legal_basis` bilgi amaçlı; KPI-008 değeri değişmez, yalnızca not üretir |
| L-15 | Puantajdaki fazla çalışma doğrulaması haftalık 45 saat esasına göre mi yapılmalı; denkleştirme (md. 63) uygulanan işyerinde kontrol devre dışı mı bırakılmalı? | Haftalık esas; yalnızca **uyarı** (hata değil) |
| L-16 | Günlük 11 saat (md. 63) ve gece 7,5 saat (md. 69) üst sınırlarını aşan puantaj satırları reddedilmeli mi, uyarıyla mı yüklenmeli? | Uyarıyla yüklenir (gerçek kaydı reddetmek ihlali gizler); grup düzeyi sayım |
| L-17 | Fesih olmadan ödenen yıllık izin ücretinin (md. 59) izin borcunu sona erdirmediği varsayımıyla istisna raporu oluşturmak uygun mu? (E.3) | Uyarı üretilir, kişi listesi yalnızca İK Operasyon'da |

## 10. Açık sorular (İK Direktörü, Agent 17)

- **S-1:** `min_feed_coverage` 0,8 (genel) / 0,9 (İSG) uygun mu? İSG için 1,0 (tam kapsam olmadan oran yok) daha güvenli olabilir.
- **S-2:** Geçiş: mevcut tenant'lar `warn` ile mi başlasın, yoksa doğrudan `enforce` mu? (`enforce` hemen birçok panoyu "veri eksik" yapar — doğru ama görünür bir değişiklik.)
- **S-3:** Dönem kapatmada zorunlu beslemesi eksik ay: uyarı mı, engel mi? (Önerim: puantaj ve İSG için engel, diğerleri uyarı.)
- **S-4:** Şantiye düzeyi toplu maliyetler (kamp, servis sözleşmesi) KPI-019'a nasıl girsin — v1 dışı mı kalsın?
- **S-5:** Büyük nüfusta "kayıt yok onaylı" izin ayı (ör. ≥ 50 kişi, 0 izin) için olasılık dışı sıfır uyarısı istenir mi?
- **S-7:** Terim birliği: kapsam beyanı alan adları (`period_start/end` ↔ Agent 18 `coverage_from/to`) ve belge adları
  (plandaki `DATA-0x` ↔ teslim edilen `WP-x`) için tek ad kümesi.
- **S-6:** Puantaj cetveli geniş formatı (sprint 2) şirketin gerçek kaynak sistemine göre önceliklendirilmeli — kaynak sistem hangisi?

## 11. Statü

**Taslak — İK Direktörü incelemesi bekliyor.** (İK Direktörü incelemesi bölümü bu satırın altına eklenecek.)

---

## İK Direktörü incelemesi (2026-11-05)

**İnceleyen:** Agent 17 (İK Direktörü) · **Karar: ŞARTLI ONAY** · Ölçütler: `HR_TEAM_PLAN.md` §2.4 ve §4.2.

### A. El hesaplarının yeniden hesaplanması

| Örnek | Benim hesabım | Sonuç |
|---|---|---|
| K1 | 1.200/150 = 8,0 sa; × 365/92 = 31,74 (KPI-008 30/20/16 → `good`) | ✔ |
| K2 | Gerekli kişi-ay 100×3 + 50×3 = 450; kapsanan 300 → C = 0,667 < 0,8 → `insufficient_data`, kalite 66,7 | ✔ |
| K3 | Kapsanan 300 + 50×2 = 400 → C = 0,889; ort. kişi 400/3 = 133,33; 1.290/133,33 = 9,675; × 365/92 = 38,38 | ✔ |
| K7 | 900/18.000 = %5,0; 300/18.000 = %1,67 | ✔ |
| K8 | 5.000.000/150 = 33.333; C = 100/150 = 0,667; 7.000.000/150 = 46.667 | ✔ |
| Örnek dosyalar | Puantaj satır 4: gece 8 > çalışılan 6 → hata ✔ (ayrıca > 7,5 uyarı koşulu da oluşur); eğitim satır 4: −4 sa → hata ✔, satır 2 İSG + `ISG_TEMEL` → `is_statutory_hse = true` ✔; maliyet satır 4: −500 düzeltmesiz → hata + `TL → TRY` uyarısı ✔ | ✔ |

### B. Onay koşulları

1. **Kapsam modeli tektir ve bu belgededir** (§3.4–3.6). WP-6 İSG belgesinin batch beyanı bu modele eşlenir; ortak ad kümesi: `period_start` / `period_end`, `completeness ∈ {complete, partial, none}` (+ satır yoksa `undeclared`), `confirmed_empty`, `attributes` (İSG: `includes_subcontractor_cases`, `includes_near_miss`), `feed_type = safety`, import tipi `safety_event`.
2. Renk/durum bastırma nedeni WP-2 koşul 1'deki ortak `status_reason` mekanizmasıyla döner (`low_coverage`); `KpiResult` şekli değişmez kuralı korunur.
3. Kapsam kapısı (§3.6) **İSG ölümlü kaza uyarısını bastırmaz** (WP-2 koşul 5).
4. §3.3.2'deki "yasal İSG eğitim asgarisi karşılanmıyor olabilir" notu, işyeri tehlike sınıfı alanı (`hazard_class`, sprint 2'de Agent 18 tanımlar — WP-6 İSG koşul 3) gelene kadar **üretilmez**; yalnızca `hours_by_category` ve `statutory_hse_hours_per_employee` kırılımı gösterilir.
5. Maliyet önizleme maskesi (§3.7.1) `cost.read` olmayan kullanıcıda **zorunlu** kabul kriteridir (L-13 yanıtına kadar güvenli varsayılan); ham maliyet satırlarının staging saklama süresi B-24 kararına bağlanır.
6. ⚖️ L-13…L-17 WP-3 dosya no. 1'de yok → hukuk soru dosyası no. 2'ye girer.

### C. Açık sorulara kararlarım

| # | Karar | Gerekçe |
|---|---|---|
| S-1 | **Genel 0,8; İSG 0,9** (1,0 değil) | 1,0 olursa tek bir geç kalan alt yüklenici tüm şirketin LTIFR'ını boşaltır ve "kayıt yok — onaylı" (`confirmed_empty`) yanlış beyanı için baskı üretir — tam da önlemek istediğimiz kaza gizleme yolu. 0,9 + "kapsam dışı firma-ay: n" notu + ölümlü kaza uyarısının kapıdan bağımsız olması daha güvenli |
| S-2 | **İSG beslemesi tüm tenant'larda hemen `enforce`**; diğer beslemeler `warn` ile başlar, 3 ay kapsam beyanı biriktikten sonra `enforce` | Bugün gerçek bir İSG import'u yok (yalnızca seed) → `enforce`'un geriye dönük kaybettirdiği bir şey yok; İSG'de yanlış sıfır en tehlikeli hatadır (M3). Diğerlerinde ani "veri eksik" dalgası kullanıcı güvenini sarsar |
| S-3 | **Engel değil, uyarı:** eksik zorunlu besleme listesi kapanış kaydına yazılır; o kapsamların dondurulmuş sonucu `insufficient_data` kalır | Kapanışı engellemek, ay sonunu kurtarmak için sahte `confirmed_empty` beyanı teşviki yaratır (özellikle İSG). D-2 (kapatma yetkisi) değişmez |
| S-4 | **v1 dışı**; KPI-019 açıklamasına "şantiye düzeyi toplu maliyetler (kamp, servis) hariç" notu | Kişiye bölünmemiş maliyeti kişiye dağıtmak keyfi anahtar ister; sprint 2 adayı |
| S-5 | **Evet:** `implausible_zero` veri kalite uyarısı — kapsamı `complete` ve ≥ 50 kişilik bir firma-ayda 0 izin kaydı (grup düzeyi, engelleyici değil). İSG karşılığı WP-2 S2 sinyalidir | Ucuz ve yanlış "tam" beyanını yakalar |
| S-6 | **Şirket girdisi gerekir** (gerçek puantaj kaynak sistemi) — `SPRINT1_REVIEW.md` §6 | Ajan karar veremez |
| S-7 | **Terim ve dosya adı kararı:** koşul 1'deki ad kümesi; dosya adları teslim edildiği gibi `WP-<n>_<konu>.md` kalır (`SPRINT1_REVIEW.md` §3) | Yeniden adlandırma çapraz referansları ve inceleme izini bozar |

### D. Statü

**İK Direktörü onaylı (şartlı)** — koşullar 1–6. Kapsam meta verisi çekirdeği (tablo + upload beyanı + kapı) yeni import tiplerinden **önce** ve ayrı paket olarak uygulanır; sonra İSG, puantaj, eğitim, maliyet sırasıyla (bkz. `SPRINT1_REVIEW.md` §5). ADR gerekli. İnsan girdisi: gerçek kaynak sistemler (puantaj, LMS, bordro), alt yüklenici sözleşmelerine veri bildirme şartı (satın alma).
