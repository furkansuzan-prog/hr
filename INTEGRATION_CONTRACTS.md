# HRSWI — Entegrasyon Sözleşmeleri

Canlı ve makine tarafından okunabilir sözleşme: **OpenAPI** → `GET /openapi.json`, etkileşimli: `/docs`.
Bu belge, OpenAPI'nin ifade edemediği **değişmezleri** (invariant) tanımlar. Değişiklik → ADR + Orchestrator onayı.

## 1. Kimlik doğrulama ve bağlam
- `Authorization: Bearer <JWT>` (HS256, 60 dk; zorunlu claim'ler: `sub`=user id, `tid`=tenant id, `tv`=token version, `iss`=hrswi, `exp`, `iat`). FAZ 7: OIDC.
- `tv` kullanıcının güncel `token_version` değeriyle eşleşmezse 401. `POST /api/v1/auth/logout-all` ve kullanıcı pasifleştirme sürümü artırır → tüm eski tokenlar geçersiz.
- Giriş koruması: üst üste 5 hatalı denemede hesap 15 dk kilitlenir (yanıt yanlış parola ile aynı genel 401 — hesap varlığı sızdırılmaz); IP başına başarısız deneme sınırı aşılırsa **429 + `Retry-After`**.
- `X-Locale: tr|en|ru` → yalnızca görüntüleme dili (KPI adları, asistan dili). Yetkiyi etkilemez.
- `X-Request-ID` (opsiyonel) → yanıtta ve loglarda aynen döner.
- Hata gövdesi: `{"detail": string | object}`; 401 kimlik, 403 yetki/kapsam, 404 yok, 409 durum çakışması (taslak KPI, kapalı dönem, mükerrer dosya), 422 doğrulama, 503 kaynak sistem erişilemez.

## 2. Ortak filtre (tablo = grafik = rapor)
`period_start`, `period_end` (ISO tarih, dahil; boşsa son 12 tam ay), `entity_ids` (virgülle), `project_id`, `occupation_group`.
Aynı filtre `/kpi/evaluate`, `/kpi/series`, `/kpi/by-entity`, `/dashboard/*`, `/reports/*` uçlarında **aynı** `Filters` sınıfıyla çözülür.
Kapsam dışı `entity_ids` istenirse **403** (sessizce daraltılmaz).

## 3. `KpiResult` (tek sayı kaynağı)
```json
{ "code": "KPI-001", "name": "…", "unit": "%", "direction": "lower_better",
  "status": "ok | not_computable | insufficient_data | suppressed",
  "value": 7.27, "annualized_value": 29.5, "threshold_status": "good|warning|critical|null",
  "status_reason": "short_window | low_exposure | unknown_category_share | low_coverage | null",
  "alerts": [{"type": "fatality", "count": 1, "by_month": {"2026-03": 1}}],
  "numerator": 2, "denominator": 27.5, "sample_size": 27, "quality_score": 50.0,
  "breakdown": {}, "lineage": {"tables": [], "...": "..."}, "note": "…",
  "period_start": "2025-01-01", "period_end": "2025-03-31",
  "scope": {"entity_ids": [..] | null, "project_id": null, "occupation_group": null},
  "kind": "actual", "formula": {"version": 1, "calculator": "turnover_rate", "text": "…", "parameters": {}},
  "thresholds": {"target": null, "warning": 30, "critical": 45}, "calculated_at": "…" }
```
Değişmezler:
1. `status != "ok"` ⇒ `value == null`. İstemci asla 0 göstermez.
2. `suppressed` ⇒ `value`, `numerator`, `denominator`, `sample_size`, `breakdown` boş.
3. Tahminler `kind: "forecast"` taşır ve gerçekleşen serilerle aynı dizide karıştırılmaz.
4. Asistan yanıtındaki her sayı, aynı yanıtın `data` / `sources` alanındaki servis çıktısından gelir.
5. `formula.version` sonuç üretildiği versiyondur; formül versiyonları değişmezdir (yeni sürüm = yeni satır).
6. **Renk bastırma (ADR-018):** `status_reason != null` ⇔ değer var ama bilerek renklendirilmedi; o zaman `threshold_status == null`. `status != "ok"` ⇒ `status_reason == null`. Tek mekanizmadır; istemci başka bir bayrak aramaz, nedeni eşik rozetinin yerinde gösterir. Kurallar formül parametresidir: `threshold_min_days` → `short_window`; `threshold_min_exposure_hours` (KPI-014A 1.000.000, KPI-014B 500.000 sa; eşitlik renkli) → `low_exposure`; `required_feeds` + `min_feed_coverage` (ADR-020) → `low_coverage` (öncelik: `low_coverage` > hesaplayıcı nedeni > `short_window`).
8. **Kapsam kapısı (ADR-020, B-20):** `required_feeds` taşıyan KPI'da kapsam oranı `C < min_feed_coverage` ise mod `warn` (varsayılan; PO-6 açık) → değer + `status_reason = "low_coverage"` + not, `quality_score ≤ C × 100`; mod `enforce` → `status = insufficient_data`, `value = null`, `quality_score = C × 100`. Mod besleme başına yapılandırmadır (`HRSWI_COVERAGE_MODES`), sonuç şekli değişmez. `lineage.coverage = {feeds, min_feed_coverage, mode, uncovered_entity_months}` (bastırılmış sonuçta yok). Eksik besleme hiçbir modda renkli gerçek sıfır olarak dönmez; ölümlü kaza uyarısı kapıdan bağımsızdır.
7. **Uyarılar (ADR-018):** `alerts` her zaman listedir (çoğu KPI'da boş). `fatality` uyarısı KPI-014A/B'de kapsam × penceredeki tüm ölümlü kaza kayıtlarını sayar; `suppressed`, `low_exposure`, meslek filtresi `insufficient_data` ve dondurulmuş dönem durumlarında da döner (ADR-009 istisnası). Yalnızca `count` ve ay düzeyinde `by_month`; kişi, açıklama veya gün içermez. İstemci uyarıyı kapatılamaz biçimde gösterir ve rengi değiştirmez. Alanlar geriye uyumlu eklemedir; eski istemciler yok sayabilir.

## 4. Yetki değişmezleri (Security ↔ herkes)
- Her iş verisi sorgusu `tenant_id` + kapsamla filtrelenir: ORM'de `AccessContext.entity_clause(perm, entity_col, project_col=… | employment_col=…)`, KPI motorunda `resolve_scope` → `calculators.Scope` (`entity_ids` + `project_limits`) → `_ent_sql` / `_emp_where`. `entity_filter(perm)` tek başına **yeterli değildir** (proje sınırlı varlıkları da içeren varlık kümesini döner); yalnızca varlık düzeyi kararlar için kullanılır (ADR-017).
- Alt yüklenici görünürlüğü yalnızca **tarihte geçerli** ve `data_sharing_granted = true` olan `contractor_relationships` ile genişler.
- **Proje sınırı (ADR-017):** `project_id` taşıyan ilişkiyle açılan alt yüklenici yalnızca o proje için görünür (`AccessContext.project_limits[perm] = {entity_id: {project_id}}`); `project_id IS NULL` ilişki bütün varlığı açar (geriye uyumlu). Proje sınırlı ebeveynden yalnızca aynı projedeki (veya NULL → ebeveynin projesi) çocuk ilişkiler izlenir. Sınırlı varlığın satırı yalnızca izinli projeye atfedilebiliyorsa görünür: satırın kendi `project_id`'si veya istihdamın pencereyle örtüşen görevlendirmesi; **`project_id` NULL satır görünmez**, proje boyutu olmayan veri (talepler, anket toplamları, aksiyonlar) sınırlı varlık için hiç görünmez.
- Proje sınırlı hibe yalnızca okumadır: yazma, import ve yönetim kontrolleri `covers_entity(perm, eid)` (bütün varlık) ister.
- Önbellek anahtarları kapsam imzasını **proje sınırlarıyla birlikte** içerir: `Scope.key()` (`…|pl:<eid>=<prj>,…`; KPI önbelleği, `kpi_results.scope_key`) ve panel anahtarı için `AccessContext.scope_signature(perm)`. `KpiResult.scope` sınır varsa `project_limits: {"<entity_id>": [project_id]}` alanını taşır (geriye uyumlu ek). `GET /auth/me` → `project_limits`.
- `sensitive` KPI'lar (maliyet) `kpi.read` **ve** `cost.read` kapsamlarının kesişimiyle hesaplanır.
- Reddedilen her erişim `audit_logs`'a `outcome='denied'` olarak ayrı transaction'da yazılır.

## 5. Veri yazma değişmezleri (Integration/Backend ↔ Analytics)
- Analitiği etkileyen **her** yazma yolu `engine.mark_stale_from(db, tenant, earliest_date)` çağırmalıdır. Bu, (a) kalıcı KPI sonuçlarını `is_stale` yapar, (b) `tenants.data_version`'ı artırarak KPI önbelleğini kesin olarak geçersiz kılar. **Bu kuralı atlayan yeni bir yazma ucu bayat sonuç döndürür** (ADR-015).
- Tarihsel kayıtlar güncellenmez; kapatılır + yeni kayıt (istihdam, görevlendirme, yüklenici ilişkisi). Geriye dönük ayrılış: `end_date` güncellenir, `record_version` artar.
- Ham kimlik numarası hiçbir tabloya yazılmaz; yalnızca `national_id_hash` (HMAC).
- **Kapsam beyanı (ADR-020):** `data_feed_coverage` her yazması (import commit, `POST /coverage`) aynı işlemde `audit_logs` (`coverage.declare`) satırı yazar ve `mark_stale_from(ilk ay)` çağırır. Yeni bir import tipi commit sonunda `app.services.coverage.record_import` (veya `declare`) çağırmalıdır; aksi halde beslemesi `undeclared` kalır ve KPI'ları renksiz görünür.

- **Puantaj (timesheets) yazmaları:** özet tablo (`timesheet_monthly`) veritabanı tetikleyicileriyle kirli işaretlenir; doğruluk için ek çağrı gerekmez. Hızlı yolun geri gelmesi için `mark_stale_from` (commit sonrası prewarm özeti yeniler) veya `app.analytics.summary.refresh` çağrılmalıdır.
- **Dönem kapatma/açma** veri sürümünü artırır (dondurulmuş değerler panel önbelleğine yansır) ve yalnızca **ana yüklenici** yetkisiyle yapılabilir (karar D-2): tenant geneli `period.close` veya kapsamında `main_contractor` firması olan kullanıcı; aksi 403.

## 6. Import sözleşmesi
`POST /imports` (multipart: `file`, `entity_type=employment`) → `{batch_id, headers, suggested_mapping, required_fields, preview}`
`POST /imports/{id}/validate` `{mapping}` → özet; hatalar `data_quality_issues` + `GET /imports/{id}/errors.csv`
`POST /imports/{id}/commit` → idempotent; `failed` durumundaki batch tekrar commit edilince kaldığı chunk'tan devam eder.
Aynı dosya (SHA-256) aynı tenant ve tipte ikinci kez commit edilemez (409).
Alanlar: `external_id*`, `source_system`, `first_name*`, `last_name*`, `national_id`, `birth_year`, `gender`, `legal_entity_code*`, `employee_number`, `contract_type`, `start_date*`, `end_date`, `termination_category`, `project_code`, `occupation_code`.

### 6.1 İzin import'u (`entity_type=leave`)
Alanlar: `external_id*` (kaynak sistem kimliği veya sicil no), `source_system`, `legal_entity_code*`, `leave_type*` (kaynak etiketi), `sgk_report_code` (1/2 → work_injury, 3 → sick, 4 → parental; ADR-019), `start_date*`, `end_date*`, `hours`, `days`, `record_id` (kaynağın rapor/izin numarası — "Rapor No", "İzin No", "Belge No"…; yeniden import kimliği, ADR-019).
- Çalışan eşleme: kimlik haritası (`person_identifiers`) → yoksa firmadaki `employee_number`; izin başlangıcını kapsayan istihdam dönemi. Bulunamazsa / birden fazlaysa satır hatası.
- İzin türü **her zaman** `app.services.timekeeping.classify_leave` / `normalize_leave_type` ile eşlenir; değer kümesi `app.models.LEAVE_TYPES` (`annual, sick, work_injury, unpaid, unexcused, public_holiday, parental, other`). Korunan izin (doğum, analık, süt, babalık, gebelik…) önce `parental`'a gider ve devamsızlık KPI'larına girmez (karar D-6). İş kazası / meslek hastalığı (SGK kodu 1/2 veya "iş kazası", "meslek hastalığı", "work injury", "occupational disease", "производственная травма"… — tek başına "kaza" değil) → `work_injury` (WP-1). Tanınmayan etiket `other` + `unmapped_leave_label` uyarısı. Uyarılar (veri kalite kuyruğu, sağlık etiketini yankılamaz): `leave_label_code_conflict` (kod esas), `unmapped_sgk_report_code`, `leave_label_ambiguous` (korunan izin + iş kazası → `parental`).
- Saat: `hours` → yoksa `days × 9` → yoksa hafta içi gün × 9 (`hours_derived` uyarısı; çalışma düzenine göre çevrim B-28).
- KVKK md.6: tanı/teşhis/ICD gibi sağlık sütunları **yüklemede okunmadan atılır** (yanıtta `dropped_columns`), hiçbir tabloya yazılmaz.
- İdempotent: aynı (istihdam, tür, başlangıç, bitiş) kaydı tekrar yazılmaz (`already_imported`); istihdam bitişini aşan izin kırpılır (uyarı).
- **Yeniden sınıflandırma (ADR-019, WP-1 §3.4):** kimlik = `record_id` varsa (istihdam, `source_system`, `record_id`), yoksa (istihdam, başlangıç, bitiş) + aile {sick, work_injury}. Aynı kimlik farklı türle gelirse kayıt **yerinde güncellenir** (`record_version + 1`, `audit_logs.action = leave.reclassify`), ikinci kayıt eklenmez; uyarı kodu `reclassified_up` (sick → work_injury) / `reclassified_down` / `leave_reclassified_protected` (parental dahil) / `leave_reclassified`. {sick, work_injury} içinde farklı tarihli örtüşme (veritabanında veya aynı dosyada) **hata** `sick_note_overlap`; aynı `record_id` farklı tarihlerle → hata `record_id_dates_changed`; diğer örtüşmeler `overlapping_leave` uyarısı. Kapalı dönemin değeri değişmez; izin KPI'larının dondurulmuş notu yeniden sınıflandırma sayısını gösterir.

### 6.2 Kapsam beyanı (`data_feed_coverage`, ADR-020, B-20)
- `POST /imports` isteğe bağlı form alanı `coverage` (JSON): `period_start` (ayın 1'i), `period_end` (ay sonu), `legal_entity_codes*`, `project_codes`, `completeness* ∈ {complete, partial, none}`, `coverage_ratio` (yalnızca `partial`, 0–1), `note`. Geçersiz → 422; kapsam dışı işveren (bütün-işveren `import.write` yok) → 403. Yanıt ve batch özeti normalize beyanı `coverage` olarak döner.
- Commit: beyan edilen her işveren × ay → satır (`basis = import_batch`; satır gelmeyen `complete` ay `confirmed_empty = true`). Beyan dışı satır: uyarı `row_outside_declared_coverage`, kapsam oluşturmaz. Beyan yoksa satır alan her işveren × ay `partial` (oran yok) — var olan beyan ezilmez.
- Yalnızca başlık satırı olan dosya: yalnızca olay beslemesi (`leave`) + `completeness = complete` ile kabul (→ kayıt yok, onaylı); aksi 400.
- `POST /api/v1/coverage` `{feed_type, legal_entity_id, project_id?, period_start, period_end, completeness, coverage_ratio?, confirmed_empty?, attributes?, planned_on_leave_days?, note?}` → 201 `{declared_rows, declaration}`. İzin: o işverende bütün-işveren `import.write`; `feed_type = safety` için ayrıca `hse.read` (geçici, A11). `none` / `confirmed_empty` → `note` ≥ 5 karakter zorunlu; gelecek ay ve > 60 ay 422. Ret `audit_logs` `outcome = denied`.
- `GET /api/v1/coverage?period_start&period_end&feed_types=` (`import.write`) → `{months, feed_types, legal_entities, cells[{feed_type, legal_entity_id, project_id, period_month, state, coverage_ratio, basis, import_batch_id, attributes, declared_at, note}], states}`; `state ∈ {complete, partial, none, confirmed_empty}`, listede olmayan hücre = `undeclared`. Yalnızca çağıranın bütün-işveren kapsamı.
- `POST /kpi/periods/{m}/close` yanıtı `coverage_gaps: [{feed_type, legal_entity_id, legal_entity_code, state}]` (aktif KPI'ların `required_feeds` birleşimi; kapatmayı engellemez, S-3).

## 7. Bağlayıcı sözleşmesi (FAZ 2+)
`Connector.fetch(since) -> Iterable[RawRecord]`; yalnızca belgelenmiş ve yetkili API'ler. Erişilemezlik → `data_sources.last_error`, **kısmi veri yazılmaz**, 503 + yeniden deneme (üstel geri çekilme).

## 8. Veri yönetimi API'si (`/api/v1/manage`, A06)
| Uç | İzin | Not |
|---|---|---|
| `GET/POST /users`, `POST /users/{id}/deactivate` | admin.users | Oluşturma 201 + tek seferlik parola; kullanıcı **silinmez**, pasifleştirilir (token iptali dahil) |
| `GET /roles`, `POST /users/{id}/roles`, `DELETE /role-assignments/{id}` | admin.users | Yetki yükseltme koruması: veren kullanıcı rolün tüm izinlerine ve hedef kapsama sahip olmalı; geri alma `valid_to` ile |
| `GET /identity-reviews`, `POST /identity-reviews/{id}/resolve` | people.write | merge / create_new / reject; payload hiçbir zaman kimlik hash'i içermez |
| `POST /people` | people.write (firma kapsamı) | Aynı kimlik hash'i → 409 `{message, candidate_person_id}` |
| `POST /employments/{id}/terminate` | people.write | İyimser kilit (`record_version`, uyuşmazlık 409 + `current_record_version`); transferle kapanmış dönem değiştirilemez (409) |
| `POST /employments/{id}/transfer` | people.write (iki firmada) | Yalnızca açık istihdam; hedefte çakışan istihdam 409; KPI-001 için iç transfer sayılır |
| `POST /legal-entities` | tenant geneli org.write | |
| `POST /contractor-relationships`, `.../{id}/end` | org.write (ebeveyn **ve** çocuk) | Paylaşım bağı ile kapsam genişletmeyi engeller |
Tüm yazmalar denetlenir ve analitiği etkileyenler `mark_stale_from` çağırır (§5).

## 9. KPI kataloğu senkronizasyonu
Kod kataloğu değişince mevcut veritabanları `python -m app.seed.reference` (kuru) / `--apply` ile eşitlenir. Formül satırları değiştirilemez olduğundan değişen hesaplayıcı/parametre **yeni formül versiyonu** olarak eklenir. Dağıtım adımıdır.
