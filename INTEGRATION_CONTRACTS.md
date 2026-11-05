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

## 4. Yetki değişmezleri (Security ↔ herkes)
- Her iş verisi sorgusu `tenant_id` + `AccessContext.entity_filter(perm)` ile filtrelenir.
- Alt yüklenici görünürlüğü yalnızca **tarihte geçerli** ve `data_sharing_granted = true` olan `contractor_relationships` ile genişler.
- `sensitive` KPI'lar (maliyet) `kpi.read` **ve** `cost.read` kapsamlarının kesişimiyle hesaplanır.
- Reddedilen her erişim `audit_logs`'a `outcome='denied'` olarak ayrı transaction'da yazılır.

## 5. Veri yazma değişmezleri (Integration/Backend ↔ Analytics)
- Analitiği etkileyen **her** yazma yolu `engine.mark_stale_from(db, tenant, earliest_date)` çağırmalıdır. Bu, (a) kalıcı KPI sonuçlarını `is_stale` yapar, (b) `tenants.data_version`'ı artırarak KPI önbelleğini kesin olarak geçersiz kılar. **Bu kuralı atlayan yeni bir yazma ucu bayat sonuç döndürür** (ADR-015).
- Tarihsel kayıtlar güncellenmez; kapatılır + yeni kayıt (istihdam, görevlendirme, yüklenici ilişkisi). Geriye dönük ayrılış: `end_date` güncellenir, `record_version` artar.
- Ham kimlik numarası hiçbir tabloya yazılmaz; yalnızca `national_id_hash` (HMAC).

- **Puantaj (timesheets) yazmaları:** özet tablo (`timesheet_monthly`) veritabanı tetikleyicileriyle kirli işaretlenir; doğruluk için ek çağrı gerekmez. Hızlı yolun geri gelmesi için `mark_stale_from` (commit sonrası prewarm özeti yeniler) veya `app.analytics.summary.refresh` çağrılmalıdır.
- **Dönem kapatma/açma** veri sürümünü artırır (dondurulmuş değerler panel önbelleğine yansır) ve yalnızca **ana yüklenici** yetkisiyle yapılabilir (karar D-2): tenant geneli `period.close` veya kapsamında `main_contractor` firması olan kullanıcı; aksi 403.

## 6. Import sözleşmesi
`POST /imports` (multipart: `file`, `entity_type=employment`) → `{batch_id, headers, suggested_mapping, required_fields, preview}`
`POST /imports/{id}/validate` `{mapping}` → özet; hatalar `data_quality_issues` + `GET /imports/{id}/errors.csv`
`POST /imports/{id}/commit` → idempotent; `failed` durumundaki batch tekrar commit edilince kaldığı chunk'tan devam eder.
Aynı dosya (SHA-256) aynı tenant ve tipte ikinci kez commit edilemez (409).
Alanlar: `external_id*`, `source_system`, `first_name*`, `last_name*`, `national_id`, `birth_year`, `gender`, `legal_entity_code*`, `employee_number`, `contract_type`, `start_date*`, `end_date`, `termination_category`, `project_code`, `occupation_code`.

### 6.1 İzin import'u (`entity_type=leave`)
Alanlar: `external_id*` (kaynak sistem kimliği veya sicil no), `source_system`, `legal_entity_code*`, `leave_type*` (kaynak etiketi), `sgk_report_code` (1/2/3 → sick, 4 → parental), `start_date*`, `end_date*`, `hours`, `days`.
- Çalışan eşleme: kimlik haritası (`person_identifiers`) → yoksa firmadaki `employee_number`; izin başlangıcını kapsayan istihdam dönemi. Bulunamazsa / birden fazlaysa satır hatası.
- İzin türü **her zaman** `app.services.timekeeping.normalize_leave_type` ile eşlenir; korunan izin (doğum, analık, süt, babalık, gebelik…) önce `parental`'a gider ve devamsızlık KPI'larına girmez (karar D-6). Tanınmayan etiket `other` + `unmapped_leave_label` uyarısı.
- Saat: `hours` → yoksa `days × 9` → yoksa hafta içi gün × 9 (`hours_derived` uyarısı; çalışma düzenine göre çevrim B-28).
- KVKK md.6: tanı/teşhis/ICD gibi sağlık sütunları **yüklemede okunmadan atılır** (yanıtta `dropped_columns`), hiçbir tabloya yazılmaz.
- İdempotent: aynı (istihdam, tür, başlangıç, bitiş) kaydı tekrar yazılmaz; çakışan izin uyarısı; istihdam bitişini aşan izin kırpılır (uyarı).

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
