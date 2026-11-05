# HRSWI — Mimari Karar Kayıtları (ADR)

Her karar: bağlam → karar → sonuç. Durum: `Kabul` / `Öneri` / `Değişti`.
Ortak API, veri modeli veya güvenlik sözleşmesini etkileyen değişiklik yeni bir ADR gerektirir (Orchestrator onayı).

---

## ADR-001 — Modüler monolit (Kabul)
**Bağlam:** 15K aktif / 100K+ tarihsel personel, 150+ kullanıcı. Bu ölçek tek PostgreSQL + tek API sürecinin yatay ölçeklenmesiyle rahatça karşılanır. Mikroservis, ilk sürümde dağıtık işlem, gözlemlenebilirlik ve yetki tutarlılığı maliyeti getirir.
**Karar:** Tek FastAPI uygulaması; `app/modules/<modül>` altında sınırları belli modüller (org, people, timekeeping, kpi, imports, forecasting, assistant, reports, risk, audit, security). Modüller birbirinin tablolarına değil servis fonksiyonlarına erişir. Ayrıştırma adayları: `imports` (worker), `forecasting`, `reports` (asenkron üretim).
**Sonuç:** Tek deploy birimi, tek migration zinciri. Ayrıştırma ileride API sözleşmeleri değişmeden yapılabilir.

## ADR-002 — Katmanlar (Kabul)
`api` (FastAPI router, şema doğrulama) → `service` (use case, iş kuralı, yetki kapsamı uygulama) → `domain` (saf hesaplama: KPI formülleri, tahmin, bastırma kuralları; DB bağımsız, birim test edilebilir) → `models` (SQLAlchemy persistence). `integration` (import adaptörleri), `analytics` (KPI motoru), `ai` (asistan + tahmin), `security` (authn/authz/audit) çapraz katmanlardır.
**Kural:** İş kuralı ve KPI hesabı frontend'de yapılmaz; frontend yalnızca API'nin döndürdüğü değeri gösterir.

## ADR-003 — Teknoloji yığını (Kabul)
- Backend: Python 3.12+ / FastAPI / Pydantic v2 / SQLAlchemy 2.0 / Alembic / Pytest.
- DB: PostgreSQL 16. Redis: önbellek + kuyruk aracısı (FAZ 3+).
- Frontend: Next.js (App Router) + TypeScript + Tailwind + ECharts + TanStack Query/Table + Zod + react-hook-form.
- shadcn/ui: bileşenler shadcn kalıbıyla (Radix-free, Tailwind + `cn()`) repo içinde tutulur; CLI ile üretim yerine elle yazıldı (çevrimdışı/CI tekrarlanabilirliği için). Radix bağımlı bileşenler (Dialog, Select) gerektiğinde CLI ile eklenecek.
- Arka plan işleri: FAZ 1–3'te import ve KPI hesaplama senkron ama **idempotent** ve `import_batches`/`kpi_calculation_runs` üzerinde durum takipli. FAZ 3 sonunda Celery + Redis'e taşınır (iş fonksiyonları zaten saf servis fonksiyonu olarak yazıldı).

## ADR-004 — Çok kiracılık (Kabul)
Paylaşımlı şema, her iş tablosunda `tenant_id` (NOT NULL, indeksli). Tüm servis sorguları `AccessContext` üzerinden tenant ve kapsam filtresi alır. FAZ 7'de PostgreSQL Row-Level Security ikinci savunma hattı olarak eklenecek (`app.current_tenant` GUC).

## ADR-005 — Yetkilendirme: RBAC + kapsam (ABAC) (Kabul)
- `roles` ↔ `permissions` (ör. `people.read`, `cost.read`, `kpi.calculate`, `report.export`, `import.write`, `admin.users`).
- `user_role_assignments(user, role, scope_type, scope_legal_entity_id)`: `scope_type ∈ {tenant, legal_entity}`.
- **Görünür tüzel kişilik kümesi** = kapsamdaki tüzel kişilikler ∪ (bu kişiliklerin `contractor_relationships` ile bağlı alt yüklenicilerinden, **ilişki tarihte geçerli ve `data_sharing_granted = true` olanlar**). Ana yüklenici erişimi otomatik ve sınırsız değildir (Master Prompt §10).
- Alan düzeyi: ücret/maliyet alanları `cost.read` olmadan API yanıtına hiç girmez; sağlık/özel nitelikli veri genel uç noktalarda yoktur.
- Her yetki kararı servis katmanında, her istekte. Asistan dahil.

## ADR-006 — Kimlik doğrulama (Kabul, kısmi)
FAZ 1: Yerel kullanıcı + parola (scrypt, stdlib) + kısa ömürlü JWT (HS256, 60 dk). Kurumsal ortam için **OIDC (Authorization Code + PKCE)** adaptörü FAZ 7'de; `security/auth.py` arayüzü (`authenticate` → `Principal`) IdP'den bağımsız tasarlandı. MFA, IdP tarafında zorunlu kılınacak. Üretimde `HRSWI_JWT_SECRET` ortamdan gelmek zorunda (varsayılan değerle `ENV=production` başlatılamaz).

## ADR-007 — Tarihsellik (Kabul)
- İş ilişkisi (`employment_relationships`), görevlendirme (`assignments`), yüklenici ilişkisi (`contractor_relationships`) ve departman bağlılığı `valid_from` / `valid_to` (yarı açık aralık, `valid_to` NULL = açık) ile tutulur; **güncelleme yerine kapatma + yeni kayıt**.
- İşveren değişikliği eski kaydı silmez/üzerine yazmaz.
- Önemli tablolar: `created_at`, `updated_at`, `source_system`, `source_record_id`, `record_version`, `updated_by`.
- Geçmiş tarihli ayrılış girildiğinde etkilenen dönemlerin KPI sonuçları `stale` işaretlenir; kapalı dönemler yalnızca yetkili "yeniden aç" işlemiyle yeniden hesaplanır.

## ADR-008 — KPI motoru (Kabul)
- `kpi_definitions` (metadata) + `kpi_formula_versions` (değişmez; yeni formül = yeni versiyon) + `kpi_calculation_runs` + `kpi_results`.
- Formüller Python'da **kayıtlı hesaplayıcılar** olarak (`analytics/calculators.py`), versiyon anahtarıyla (`KPI-001@1`). Kullanıcı tanımlı serbest formül ifadesi (DSL) FAZ 4'e ertelendi — keyfi ifade değerlendirme güvenlik ve doğrulama riski taşır.
- Sonuç durumları: `ok`, `not_computable` (payda 0), `insufficient_data` (kaynak veri yok/eksik), `suppressed` (küçük örneklem). **Payda 0 → değer NULL, asla 0.**
- Her sonuç pay, payda, kaynak kayıt sayıları, veri kalite puanı, formül versiyonu, hesaplama zamanı ve kapsam (filtre) özetini taşır → kaynak özete inilebilir.
- Yalnızca `status='active'` ve `validated=true` olan tanımlar üretimde hesaplanır; katalogdaki diğerleri `draft`.

## ADR-009 — Küçük örneklem bastırma (Kabul)
Kişi sayısına dayanan toplu göstergelerde segment n < `HRSWI_MIN_GROUP_SIZE` (varsayılan 5) → `suppressed`. Anket sonuçlarında eşik 10 ve yalnızca `survey_aggregates` saklanır (bireysel yanıt tutulmaz).

## ADR-010 — Yapay zekâ sınırları (Kabul)
- Asistan **araç çağıran yönlendirici**dir: niyet → onaylı servis (KPI motoru, tahmin, anomali). Sayılar yalnızca servislerden gelir; yanıt şablonu sayıları servis çıktısından enjekte eder.
- FAZ 1–3: deterministik çok dilli niyet sınıflandırıcı (TR/EN/RU anahtar kelime). FAZ 6: LLM (ör. Claude) yalnızca *niyet çıkarımı ve anlatı* için; LLM'e kişisel veri gönderilmez, yalnızca toplu/anonim servis çıktısı. Varsayılan kapalı (`HRSWI_LLM_ENABLED=false`).
- Asistan her çağrıda kullanıcının `AccessContext`'i ile çalışır; DB'ye doğrudan erişimi yoktur.
- Bireysel çıkarma/ücret/disiplin/terfi kararı, korunan özelliklere dayalı bireysel risk puanı **üretilmez**; bu niyetler reddedilir ve gerekçesi yazılır.
- Her çağrı `ai_analysis_runs` tablosuna (soru, niyet, kullanılan araç, kapsam) loglanır.

## ADR-011 — Tahminleme (Kabul)
FAZ 3: Açıklanabilir istatistiksel model — doğrusal trend + (≥24 nokta varsa) mevsimsel bileşen, artıkların standart sapmasıyla %80/%95 tahmin aralığı, geriye dönük (holdout) MAPE raporu. < 12 aylık nokta → `insufficient_data` (Test senaryosu 18). Tahminler `forecast_results` tablosunda gerçekleşmiş veriden ayrı tutulur ve API'de `kind: "forecast"` olarak işaretlenir.

## ADR-012 — Veri içe aktarma (Kabul)
Aşamalar: `upload → preview (alan eşleştirme önerisi) → validate → commit`. Dosya SHA-256 ile `import_batches` idempotency anahtarı; aynı dosya ikinci kez commit edilmez. Satır hataları `data_quality_issues`'a. Kişi eşleştirme: tenant içinde `national_id_hash` (HMAC-SHA256, tuzlu) — çakışma/şüpheli eşleşme `identity_review` kuyruğuna. Ham kimlik numarası saklanmaz. Büyük dosyalar 1.000 satırlık parçalarla işlenir.

## ADR-013 — Gözlemlenebilirlik (Öneri → FAZ 7)
Yapılandırılmış JSON log (request id, user id, tenant id — kişisel veri yok), `/health/live`, `/health/ready` (DB kontrolü), Prometheus `/metrics` ve OpenTelemetry FAZ 7.

## ADR-014 — Para birimi (Kabul)
Maliyet kayıtları `amount`, `currency` (ISO 4217), `cost_date`, `cost_scope` taşır. Raporlama para birimine çevrim `fx_rates(date, from, to, rate, source)` ile; kur bulunamazsa sonuç `insufficient_data` (sessiz varsayım yok).
