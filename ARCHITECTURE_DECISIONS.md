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

## ADR-015 — KPI sonuç önbelleği ve kesin geçersizleştirme (Kabul)
**Bağlam:** İlk ölçümde 30 eşzamanlı istekte dashboard p95 9.8 sn (bkz. `docs/PERFORMANCE_REPORT.md`); bir dashboard ~75 KPI hesaplaması yapıyor.
**Karar:** KPI hesap sonucu (dil bağımsız `CalcResult`) şu anahtarla önbelleklenir: `tenant : tenants.data_version : formula_version_id : scope_key : period`. Kapsam anahtarı yetki çözümlemesinden **sonra** üretildiği için farklı kapsamlar aynı girdiyi paylaşamaz. Analitiği etkileyen her yazma yolu `data_version`'ı artırır (`engine.mark_stale_from`) → eski girdiler bir daha okunmaz; TTL (15 dk) yalnızca güvenlik ağıdır. Redis (`HRSWI_REDIS_URL`) worker'lar arası paylaşım sağlar; yoksa süreç içi sınırlı LRU. İstek içinde KPI tanımı/formül/data_version için oturum düzeyi memo.
**Sonuç:** Sıcak önbellekte dashboard p95 1.06 sn (30 eşzamanlı). Yeni bir yazma ucu `mark_stale_from` çağırmazsa bayat sonuç döner — `INTEGRATION_CONTRACTS.md §5` bu kuralı sözleşme olarak tanımlar ve import testi geçersizleştirmeyi doğrular.

## ADR-016 — Önceden toplanmış puantaj özeti ve önbellek ısıtma (Kabul)
**Bağlam:** 15K personelde soğuk panel 19.8 sn, import sonrası 150 kullanıcıda p95 117 sn ve %8 sunucu hatası (A13 ölçümü).
**Karar:** (1) `timesheet_monthly` özet tablosu; doğruluğu **veritabanı tetikleyicileri** garanti eder: puantajdaki her değişiklik ilgili ayı kirli işaretler ve kirli aylar ham tablodan okunur — özet hiçbir koşulda yanlış KPI üretemez, yalnızca hızlı yolu kapatır. (2) Soğuk anahtar için tek hesaplayıcı kilidi (dogpile). (3) Analitiği değiştiren commit sonrası arka planda özet yenileme + kullanıcı kapsamları başına panel ısıtma. (4) Panel yanıtının bütün olarak, kapsam imzalı anahtarla önbelleklenmesi; dönem kapatma/açma veri sürümünü artırır.
**Sonuç:** 150 kullanıcıda tüm senaryolarda panel p95 < 200 ms, hata yok (`docs/PERFORMANCE_REPORT.md`). Isıtma şimdilik süreç içi tek iş parçacığında; iş kuyruğuna taşınması B-12.

## ADR-017 — Proje kapsamlı alt yüklenici görünürlüğü (Öneri → Orchestrator birleştirmede onaylar; PO-2(a))
**Bağlam:** `security/access.py` `_shared_subcontractors` `contractor_relationships.project_id`'yi yok sayıyordu: tek bir projede verilen paylaşım izni alt yüklenicinin **tüm projelerdeki** iş gücü verisini, kademeli olarak da alt kademeleri herhangi bir proje üzerinden açıyordu (HR sprint-1 doğrulama notu b, WP-3 SUB-02, `docs/hr/SPRINT1_REVIEW.md` §5 dalga 1 madde 2). D-1 projeler arası açılımı karara bağlamadığı için bu bir **hata düzeltmesidir** (PO-2(a) önerisi). Kademe (2. kademe yalnızca toplu, PO-2(b)) ve geçmiş dönem (ilişki dönemleri, PO-2(c)) kuralları bu ADR'nin **kapsamı dışındadır**; ürün sahibi kararını bekler.
**Karar:**
1. **Kapsam iki türlü hibedir:** (a) varlık düzeyi — kullanıcının kendi tüzel kişiliği / tenant ve `project_id IS NULL` ilişkilerle açılan alt yükleniciler (bugünkü davranış, geriye uyumlu); (b) **proje sınırlı varlık hibesi** — `project_id = P` olan ilişkiyle açılan alt yüklenici yalnızca P için görünür.
2. **Kademeli yayılım:** proje sınırlı bir ebeveynden (projeler L) yalnızca `project_id ∈ L` olan çocuk ilişkiler izlenir (çocuk sınırı = o proje); `project_id IS NULL` çocuk ilişki ebeveynin projelerine (L) sınırlanır. Bütün-varlık yolu her zaman proje sınırlı yolu ezer; birden çok sınırlı yol projelerini birleştirir.
3. **`AccessContext`:** `grants[perm]` aynı kalır (varlık kümesi, sınırlı varlıklar dahil — mevcut çağıranlar çalışır). Yeni `project_limits[perm] = {entity_id: {project_id}}`; yardımcılar `limits(perm)`, `scope_for(perms)` (çoklu izin kesişimi: sınırlı varlık sınırlı kalır, proje kesişimi boşsa varlık düşer), `entity_clause(perm, entity_col, project_col=|employment_col=)`, `covers_entity(perm, eid)` (bütün varlık), `employment_visible`, `scope_signature(perm)`.
4. **Satır kuralı:** proje sınırlı varlığın verisi yalnızca izinli bir projeye **atfedilebiliyorsa** görünür. Kendi `project_id`'si olan satırlar (puantaj, `timesheet_monthly`, maliyet, İSG olayı, işgücü planı, duruş, risk, görevlendirme, iş çıktısı → iş paketi projesi) o sütunla; istihdam türevli veriler (kişi listesi, kişi sayısı, ayrılış/işe alım, izin, eğitim, cinsiyet/kıdem) istihdamın değerlendirme penceresiyle örtüşen izinli projede görevlendirmesi ile. **`project_id` NULL satır görünmez**; proje boyutu olmayan veri (talep/işe alım süresi, anket toplamları, iyileştirme aksiyonları) sınırlı varlık için hiç görünmez.
5. **KPI motoru:** `calculators.Scope.project_limits` (sıralı demet) → `Scope.key()` → KPI sonuç önbelleği, dondurulmuş sonuç (`kpi_results.scope_key`) ve panel yanıt anahtarı (`scope_signature`) proje sınırlarını içerir; farklı sınırlar aynı girdiyi paylaşamaz. Tüm SQL varlık filtreleri `_ent_sql` / `_emp_where` üzerinden geçer.
6. **Yazma:** proje sınırlı hibe yalnızca okuma görünürlüğüdür; yazma/yönetim/import kontrolleri `covers_entity` (bütün varlık) ister; dönem kapatma yetkisi (D-2) yalnızca bütün-varlık hibesindeki ana yükleniciyle.
**Sonuç:** Şema değişikliği / migration yok. Ana yüklenicinin proje sınırlı ilişkilerdeki görünümü daralır; `project_id` NULL ilişkiler aynen kalır. Bilinen yaklaşıklık: izin kaydının projesi yok — pencere içinde izinli projede görevlendirmesi olan çalışanın penceredeki tüm izni sayılır. Kapalı dönemde sınırlı kapsam için dondurulmuş sonuç bulunmaz (dondurma tenant + tek varlık kapsamlarında yapılır), değer canlı hesaplanır — çok varlıklı kapsamlarla aynı davranış. Testler: `tests/test_project_scoped_access.py`.

## ADR-018 — Renk bastırma nedeni (`status_reason`) ve orandan bağımsız uyarılar (`alerts`) (Öneri → Orchestrator onayı)
**Bağlam:** `SPRINT1_REVIEW.md` ADR-a. İSG oranlarında küçük paydada tek olay rengi belirliyordu (WP-2 R1); ölümlü kaza yalnızca oranın içinde bir sayıydı ve oran gizlenince/renksiz kalınca görünmez olabiliyordu (WP-2 R2). KPI-002 kısa pencerede sahte "yeşil" üretiyordu (WP-5 §3.6). WP-5 ayrıca ayrı bir `threshold_suppressed` bayrağı önermişti; İK Direktörü **tek mekanizma** kararı verdi (WP-2 inceleme koşulu 1, SPRINT1_REVIEW §3).
**Karar:**
1. `KpiResult`'a geriye uyumlu iki alan eklenir: `status_reason: "short_window" | "low_exposure" | "unknown_category_share" | "low_coverage" | null` ve `alerts: [{type, count, by_month}]` (her zaman var, boş liste olabilir). Mevcut alanların anlamı değişmez.
2. **Renk bastırma tek kuraldır:** değer gösterilir ama renklendirilmezse `threshold_status = null` ve **tek** bir `status_reason` dolar. Değer yoksa (`status != ok`) `status_reason = null` (durumun kendisi açıklar). Öncelik: hesaplayıcının verdiği neden (ör. `low_exposure`) → motorun pencere kuralı (`threshold_min_days` → `short_window`). Yeni nedenler (`unknown_category_share` KPI-020 v2, `low_coverage` kapsam kapısı) aynı alanı kullanır; yeni bayrak açılmaz.
3. `CalcResult.status_reason` hesaplayıcı tarafından doldurulur. `kpi_results` tablosunda kolon yoktur (migration gerektirmez): dondurulmuş sonuç için neden `lineage._status_reason` anahtarında saklanır ve okunurken API'den çıkarılır — kapalı dönemde de canlı sonuçtaki renk kuralı korunur. Önbellek şeması `v3`.
4. Yeni formül parametresi `threshold_min_exposure_hours` (tam sayı, 1…10.000.000; hesaplayıcı beyaz listesi: `ltifr`, `trir`). Çalışılan saat < değer ⇒ `low_exposure` (eşitlik renkli). Katalog: KPI-014A 1.000.000 sa, KPI-014B **500.000 sa** (İK Direktörü WP-2 Q2). Küçük kapsam hiçbir pencerede renk almaz (Q1 = C1; genişleyen pencere yok). KPI-002: `threshold_min_days = 360`, yıllıklaştırma yok.
5. **Ölümlü kaza uyarısı** (`alerts[].type = "fatality"`): `ltifr`/`trir` sonuçlarında, kapsam × penceredeki **tüm** `fatality` kayıtlarını sayar (formülün `event_types`'ından bağımsız). Oranın maruziyet kuralından, küçük grup bastırmasından (ADR-009 istisnası, İK Direktörü WP-2 Q7 — ⚖️ KVKK teyidi WP-3'te), meslek filtresinden (olaylar meslek bazında tutulmaz; filtre uyarıda yok sayılır) ve dönem dondurmadan etkilenmez — uyarı her zaman canlı veriden hesaplanır (`data_version` anahtarlı önbellek). Yalnızca sayı ve ay (`YYYY-MM`); kişi, açıklama, gün düzeyinde tarih yok. Görünürlük KPI'yı o kapsamda okuyabilen herkestir; kapsam yetki çözümlemesinden sonra gelir, genişletilmez. Uyarı rengi zorla kırmızıya çevirmez.
6. Formül satırları değişmez: katalog parametre değişiklikleri mevcut veritabanlarına `python -m app.seed.reference --apply` (katalog senkronizasyonu) ile **yeni formül versiyonu** olarak girer; eski versiyon numarasıyla hesaplama eski davranışı korur.
**Sonuç:** İstemciler `status_reason` ve `alerts`'ı opsiyonel okur (frontend tipi opsiyonel; `/hse` kapatılamaz ölümlü kaza bandı + kutuda neden rozeti ve "olay / saat"). Açık: kategori kırılımı (`by_worker_category`) ve yeniden sınıflandırma işareti (WP-2 Örnek L) `safety_event` import'u ile gelir (ADR-f, WP-6 İSG); alt yüklenici ölümlü kazasının paylaşım izni olmadan gösterilmesi ürün sahibi kararı PO-3 ve A11 kapsam işine bağlıdır. Testler: `tests/test_safety_exposure.py`.

## ADR-019 — İzin türü kümesine `work_injury` ve izin yeniden sınıflandırma kuralı (Öneri → Orchestrator onayı)
**Bağlam:** `SPRINT1_REVIEW.md` ADR-b, dalga 1 madde 5 ve 6; kaynak `docs/hr/hse/WP-1_work_injury_leave.md` + İK Direktörü incelemesi (koşul 1–7). İş kazası / meslek hastalığı istirahati `sick` olarak içe alınıyor ve KPI-028/053'e giriyordu (kaza gizleme baskısı, WP-1 Örnek 1b); uzun yokluk listesinde olmadığından yaralanan kişi KPI-056'da "izin kullanmayan" görünüyordu (Örnek 3). Aynı rapor sonradan farklı türle geldiğinde import ikinci bir kayıt ekliyordu (çift sayım, Örnek 2). Ürün sahibi kararı **PO-1** (KPI-003'ten dışlama, seçenek C) **henüz verilmedi**.
**Karar:**
1. **Değer kümesi** `app.models.operations.LEAVE_TYPES` = `annual, sick, work_injury, unpaid, unexcused, public_holiday, parental, other` (tek kaynak; `engine._LEAVE_TYPES`, `timekeeping.classify_leave` ve katalog buradan okur). `leave_records.leave_type` `String(20)`, DB CHECK/enum yok → **migration yok**. Tek tür: iş kazası + meslek hastalığı (WP-1 Q1). Ailesi sabitleri: `SICK_NOTE_FAMILY = {sick, work_injury}`, `LONG_ABSENCE_TYPES = (sick, parental, unpaid, work_injury)` (WP-4 koşul 3: tek sabit, sonraki KPI-055/056 versiyonu devralır).
2. **Eşleme** (`timekeeping.classify_leave` → `(tür, uyarılar)`; `normalize_leave_type` aynı türü döndürür): SGK rapor türü 1, 2 → `work_injury`; 3 → `sick`; 4 → `parental`. Tanınan kod etiketi ezer; etiket farklı bir tür adlandırıyorsa `leave_label_code_conflict` (yalnızca "rapor/istirahat" gibi genel etiketler çelişki sayılmaz). Tanınmayan dolu kod → `unmapped_sgk_report_code`, etikete düşer. Etiket sırası `parental → public_holiday → unexcused → work_injury → sick → annual → unpaid → other`, Türkçe güvenli katlama. İğneler WP-1 §3.2 + koşul 4: tek başına "kaza / accident / occupational / несчастн" yok. Etikette hem korunan izin hem iş kazası varsa `parental` + `leave_label_ambiguous` (D-6). Veri kalite mesajları sağlık etiketini yankılamaz (WP-1 §3.6).
3. **KPI parametreleri:** yeni `excluded_types` (`absenteeism_rate`): verilirse `absence_types ∪ excluded_types = LEAVE_TYPES` ve kesişim boş olmalı (doğrulayıcı) — yeni bir tür bir devamsızlık KPI'sından **listede olmadığı için sessizce düşemez**. Katalogdaki tüm `absenteeism_rate` KPI'ları her türü açıkça sınıflandırır (test). Yeni `long_absence_types` (`annual_leave_nonuse`); yoksa eski liste `(sick, parental, unpaid)` — eski versiyonun anlamı korunur.
4. **Katalog (senkronizasyonla yeni formül versiyonları):** KPI-003 `work_injury` **açıkça dahil** (seçenek A, koşul 1 — sayı değişmez); KPI-028 ve KPI-053 `work_injury` **hariç** (parametre `absence_types` aynı, `excluded_types` eklendi; KPI-028 adı "iş kazası hariç"); KPI-056 `long_absence_types = LONG_ABSENCE_TYPES`. KPI-055 değişmez (uzun yokluk okumaz). Eski sonuçlar eski versiyon numarasıyla korunur.
5. **Kırılım maskeleme (kabul 9):** `absenteeism_rate` kırılımında `work_injury` saati, dönem × kapsamda bu türde kaydı olan farklı kişi 1…`HRSWI_MIN_GROUP_SIZE`−1 ise `null`, `breakdown.masked_types` listesi. Değer/pay değişmez. (`parental` maskelenmez; KPI-054 kendi kuralını uygular.)
6. **Yeniden sınıflandırma (import, WP-1 §3.4):** yeni opsiyonel alan `record_id` (rapor/izin no) → `leave_records.source_record_id` (yoksa eski davranış: çalışan dış kimliği). Kimlik: `record_id` varsa (istihdam, kaynak sistem, `source_record_id`); yoksa (istihdam, başlangıç, bitiş) **ve** tür ailesi {sick, work_injury}. Aynı anahtar + farklı tür = **yerinde güncelleme** (`leave_type`, `record_version + 1`, aynı işlemde `audit_logs` satırı `action = leave.reclassify`, `details = {from, to, direction, legal_entity_id, start_date, end_date, import_batch_id}`); ikinci kayıt eklenmez. Yön kural kodları (koşul 5): `reclassified_up` (sick → work_injury), `reclassified_down` (ters), `leave_reclassified_protected` (parental dahil), diğerleri `leave_reclassified`. {sick, work_injury} içinde farklı tarihli örtüşme (veritabanında veya aynı dosyada) **hata** `sick_note_overlap`; aynı `record_id` farklı tarihle → hata `record_id_dates_changed`; diğer örtüşmeler bugünkü gibi uyarı.
7. **Kapalı dönem (D-2):** dondurulmuş değer değişmez; izin kaynaklı KPI'larda (`absenteeism_rate`, `protected_leave_share`, `annual_leave_*`) bayat dondurulmuş sonucun notuna "N leave record(s) reclassified after closing" eklenir (yalnızca sayı; kapsamın bütün varlıkları).
8. **Yön sayaçları:** `imports.leave_reclassification_counts(db, ctx, ps, pe)` — çeyrek × tüzel kişilik, yalnızca sayı; çeyrek toplamı < `HRSWI_MIN_GROUP_SIZE` olan firma yalnızca `other_entities` kalanında ve toplamda görünür; `timekeeping.read` bütün-varlık kapsamı. API ucu yok (sonraki iş, A06/A11).
**PO-1 kararı geldiğinde (seçenek C):** yalnızca katalog düzenlemesi — KPI-003'te `"work_injury"` `absence_types`'tan `excluded_types`'a taşınır, `incl/excl` metni güncellenir, `python -m app.seed.reference --apply` yeni formül versiyonu açar (Örnek 1: %7,30 → %6,50; `test_po1_option_c_is_a_parameter_change_only`), `DECISIONS.md`'ye D-3 revizyon satırı. Eşiksiz "İş Kazası/Meslek Hastalığı İstirahat Payı" KPI'sı PO-1 **ve** PO-5'e bağlıdır (dalga 2 madde 11) — bu ADR'de yok.
**Sonuç:** Migration yok (`alembic check` temiz). Kapsam dışı (açık): geçmiş veri taşıma (WP-1 §3.7, madde 11), izin ↔ İSG uyum sinyali (§3.5, İSG import'una bağlı), sağlık türevli KPI erişim izni (PO-5), yön sayacı API'si, KPI-003 pay − diğer KPI'lar farkıyla `work_injury` saatinin dolaylı çıkarımı (maskeleme yalnızca kırılımı korur; PO-5/A11 ile ele alınmalı). Testler: `tests/test_work_injury.py`, `tests/test_leave_policy.py` (5 satır bilinçli olarak `work_injury`'ye çevrildi, koşul 2).
