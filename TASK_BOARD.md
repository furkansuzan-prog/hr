# HRSWI — Görev Panosu

Durum: `Done` (kod + test geçti) · `Partial` · `Todo`. "Test kanıtı" sütunu yalnızca **çalıştırılmış** testleri listeler.
Son doğrulama: backend `pytest` (gerçek PostgreSQL), frontend `tsc` + `eslint` + `next build`, `e2e/smoke.mjs` 12/12.

## Tamamlanan / devam eden görevler (v0.1)
| ID | Görev | Ajan | Ön koşul | Kabul kriteri | Durum | Test kanıtı |
|---|---|---|---|---|---|---|
| T-000 | Envanter, varsayımlar, plan | 00 | — | PROJECT_PLAN + AGENTS | Done | — |
| T-001 | PRD, hikâyeler, ekran haritası | 01 | T-000 | 16 hikâye + kabul kriterleri | Done | — |
| T-002 | İK süreç ve metrik anlamları | 02 | T-001 | Ayrılış kategorileri, transfer kuralı, devamsızlık tanımı | Done | `test_turnover_*` |
| T-003 | Standart eşleştirme | 03 | T-005 | 6 çerçeve, gereklilik→KPI, uyum iddiası yok | Done | `test_sustainability_coverage_disclaimer` |
| T-004 | Meslek grubuna göre verimlilik modeli | 04 | T-001 | KPI-005 yalnızca standardı olan işlerde | Done | `test_kpi_*`, canlı API |
| T-005 | Veri modeli + migration'lar | 05 | T-002 | 58 tablo, tarihsellik, kısıtlar, `0001`,`0002` | Done | testler şemayı migration'la kurar; `test_health` (head=0002) |
| T-006 | JWT auth + RBAC + ABAC kapsamı | 11 | T-005 | Alt yüklenici izolasyonu, paylaşım izni, tarih | Done | `test_security.py` (12) |
| T-007 | KPI motoru (versiyon, durum, köken, dönem kapanışı) | 09 | T-005 | Payda 0 ≠ 0; eski versiyon korunur; kapalı dönem 409 | Done | `test_formula_change_preserves_old_results`, `test_period_close_blocks_recalculation`, `test_domain.py` |
| T-008 | 18 aktif KPI hesaplayıcısı | 09 | T-007 | El hesabıyla eşleşme | Done | `test_kpi_exact.py` (15) |
| T-009 | KPI kataloğu (52) | 02/09 | T-007 | Zorunlu metadata, taslaklar çalışmaz | Done | `test_catalog_has_50_plus_and_only_validated_active`, `test_draft_kpi_cannot_run` |
| T-010 | Import hattı (önizleme→eşleştirme→doğrulama→commit) | 08 | T-005, T-006 | İdempotent, devam edebilir, kimlik eşleştirme, hata raporu | Done | `test_imports.py` (5) |
| T-011 | Puantaj kuralları (mükerrer kart, gece vardiyası) | 08 | T-005 | Dakika içi tekrar tek olay; gece saatleri ayrımı | Done (servis) / UI Todo | `test_dedup_key_same_minute`, `test_night_shift_split_across_days`, `test_duplicate_badge_events_ingested_once` |
| T-012 | Tahmin + anomali | 10 | T-007 | Aralıklar, geri test, yetersiz veri reddi, mevsimsellik | Done | `test_forecast_*`, `test_robust_anomaly` |
| T-013 | HR Intelligence Assistant | 10 | T-007, T-012 | 8 örnek soru, TR/EN/RU, yasak kararlar, kapsam | Done (deterministik) | `test_assistant_*` (6) |
| T-014 | Raporlar (aylık İK, alt yüklenici) XLSX/PDF/CSV | 14 | T-007 | Meta veri, export izni, maliyet atlama | Done | `test_reports_all_formats`, `test_report_omits_cost_without_permission`, `test_unauthorised_report_download_denied_and_audited` |
| T-015 | REST API + OpenAPI | 06 | T-006..T-014 | Ortak filtre, hata kodları | Done | tüm API testleri, `test_same_filter_for_table_and_chart` |
| T-016 | Frontend: kabuk, tasarım sistemi, i18n, tema | 07 | T-015 | TR/EN/RU, koyu tema, mobil, a11y temel | Done | E2E: i18n, dark, mobile |
| T-017 | Frontend: dashboard, KPI katalog/detay, personel, yükleniciler, asistan, rapor, import, sürdürülebilirlik, risk, verimlilik | 07 | T-016 | Gerçek API, sahte değer yok, demo etiketi | Done | E2E 12/12 |
| T-018 | Sentetik veri üretici | 12 | T-005 | Deterministik, gerçek kişi verisi yok | Done | test fixture'ı |
| T-019 | Docker, Compose, CI | 13 | — | `docker compose build`, CI kapıları | Partial | web imajı derlendi; API imajı Docker Hub 429 nedeniyle bu ortamda derlenemedi; CI GitHub'da henüz çalıştırılmadı |
| T-020 | Performans ölçümü + önbellek | 13/09 | T-015 | p95<2 sn (30 eşzamanlı) | Partial | 30: ✔ 1.06 sn · 150 düşünme süresiz: ✖ 3.6 sn (`PERFORMANCE_REPORT.md`) |
| T-021 | Dokümantasyon (README, KPI sözlüğü, veri sözlüğü, yetki matrisi) | 15 | — | Koddan üretilen referanslar | Done | `scripts/gen_docs.py` |
| T-022 | Bağımsız denetim | 16 | T-001..T-021 | Bulgu raporu | Done | `docs/AUDIT_REPORT.md` (20 bulgu: 7 yüksek) |
| T-023 | Denetim bulgularının düzeltilmesi | 06/09/11 | T-022 | Yüksek bulgular kapalı + regresyon testi | Partial | 14 düzeltildi, 5 kısmen, 1 açık (politika) · `test_audit_regressions.py` (11) |

## Sprint 2 — paralel ajan çalışması (5 uzman alt ajan + orchestrator entegrasyonu)
| ID | Görev | Ajan | Durum | Test kanıtı |
|---|---|---|---|---|
| T-030 | Veri yönetimi API'si (kullanıcı/rol, kişi/istihdam, transfer, kimlik kuyruğu, firma/yüklenici ilişkisi) | A06 | Done | `test_data_management.py` (9) |
| T-031 | 10 KPI aktivasyonu (020–023, 028, 032, 034, 036, 048, 050), dönem kapanışında dondurma, toplu seri | A09 | Done | `test_kpi_extended.py` (27) |
| T-032 | Katalog senkronizasyonu (mevcut DB'ler) | 00 | Done | `test_catalog_sync.py` (2); dev DB'ye uygulandı (28 aktif KPI) |
| T-033 | İSG, Eğitim, Maliyet, Planlama ekranları; yönetim UI; erişilebilir diyalog | A07 | Done | E2E 29/29 |
| T-034 | Hesap kilitleme, IP limiti, token iptali (`tv`), saklama-imha servisi; migration 0004 | A11 | Done | `test_security_hardening.py` (11) |
| T-035 | `/metrics`, 15K ölçek testi, yedek/geri yükleme tatbikatı, CI perf işi | A13 | Done (ölçüm) | `test_metrics.py`; tatbikat PASS (4.46M satır) |
| T-036 | Çift transfer hatası düzeltmesi, kullanıcı pasifleştirme, dev DB onarımı | 00 | Done | `test_data_management.py` |


Değerlendirme: iş değeri / kullanıcı önemi / maliyet / bağımlılık / veri bulunabilirliği / güvenlik etkisi / operasyonel risk.

| ID | Öncelik | Görev | Ajan | Gerekçe |
|---|---|---|---|---|
| B-01 | P0 | OIDC + MFA, httpOnly oturum (BFF), rate limit | 11 | Kurumsal kullanım ön koşulu; XSS ile token çalınma riski |
| B-02 | P0 | PostgreSQL RLS (ikinci savunma hattı) | 05/11 | Yetki hatası = veri sızıntısı |
| B-03 | P0 | Saklama-imha işleri (staging ham satırları dahil) | 08/11 | KVKK md.7 |
| B-04 | P0 | 15K/100K ölçek verisiyle performans + düşünme süreli 150 kullanıcı yük testi | 13 | Hedef doğrulanmadı |
| B-05 | P0 | Yedekleme/geri yükleme tatbikatı, izleme panelleri, `/metrics` | 13 | Operasyonel süreklilik |
| B-06 | P1 | Org/personel/istihdam CRUD UI + kimlik inceleme kuyruğu UI | 06/07 | FAZ 2 tamamlama |
| B-07 ◐ | P1 (izin import'u Done; puantaj, maliyet, eğitim, İSG tipleri açık) | Puantaj, izin, maliyet, eğitim, İSG import tipleri + bağlayıcı çerçevesi | 08 | Gerçek veri girişi |
| B-08 | P1 | Kullanıcı/rol atama UI | 07/11 | Yönetim |
| B-09 | P1 | Tenant'a özel formül parametreleri | 09 | Kurumlar arası farklı tanımlar |
| B-10 | P1 | Taslak KPI'ların (020–051) doğrulanıp aktive edilmesi | 02/09 | MVP iş değeri |
| B-11 | P1 | Risk/aksiyon oluşturma-düzenleme, kanıt yükleme | 06/07 | Modül K/M |
| B-12 | P1 | Celery: asenkron rapor/import/hesaplama, planlanmış raporlar | 13/14 | Ağır iş yükleri |
| B-13 | P1 | Toplu aylık seri hesaplayıcıları (tek SQL) | 09 | Soğuk önbellek performansı |
| B-14 | P2 | LLM ile niyet çözümleme/anlatı (yalnızca toplu veri) | 10 | Kullanılabilirlik |
| B-15 | P2 | Senaryo simülasyonu, iş paketi bazlı planlama | 04/10 | FAZ 5–6 |
| B-16 | P2 | Çevresel etki modülü (KPI-052) | 03 | Opsiyonel modül N |
| B-18 | P0 | Dönem kapanışından önce KPI çalıştırmasını zorunlu/otomatik yapmak (denetim #6 notu) | 09 | Kapalı dönemin her zaman dondurulmuş değeri olsun |
| B-19 ✔ | Karar D-1 | Alt yüklenici görünürlüğü: dönem bazlı mı bugünkü izin mi? (denetim #16, ⚖️) | 01/11 | Politika kararı |
| B-20 | P1 | Veri kaynağı kapsam meta verisi: eksik besleme ≠ gerçek sıfır (denetim #19) | 08/09 | Yanlış "0" riskini kapatır |
| B-21 ✔ | P0 (Done: 15K'da 150 VU soğuk/import sonrası p95 72–183 ms; `test_timesheet_summary.py`) | Soğuk önbellek performansı: aylık puantaj özet tablosu, maliyet sorgusunda `count(DISTINCT)` kaldırma, tek-hesaplayıcı kilidi, import sonrası önbellek ısıtma (A13 ölçümleri: soğuk dashboard 19.8 sn) | 09/05 | 15K'da soğuk p95 117 sn |
| B-22 ◐ | P0 (503 + thread limiter Done; PgBouncer/üretim ayarı açık) | Havuz zaman aşımında 500 yerine 503; worker thread sayısı ↔ DB havuzu uyumu / PgBouncer | 13 | Yük altında 500 hataları |
| B-23 | P1 | Sentetik üreticide akışlı COPY (1.4 GB bellek tepesi) | 12 | Ölçek verisi üretimi |
| B-24 | P1 | Saklama-imha işinin zamanlanması (Celery beat / cron) | 11/13 | KVKK |
| B-25 ✔ | P1 (D-5) | İK yöneticisinden KPI hedef/eşiklerinin alınması ve kataloğa işlenmesi (D-4, `docs/KPI_TARGETS_REQUEST.md`) | 02/09 | Renkli durum göstergesi için gerekli |
| B-26 ✔ | P2 (D-6) | Doğum/ebeveyn izninin devamsızlığa dahil olup olmadığı (D-3 açık nokta) | 02 | İK onayı |
| B-27 ✔ | P1 (izin import'u `normalize_leave_type` kullanıyor; `test_leave_import.py`) | İzin import'u (B-07 kapsamı) `normalize_leave_type` kullanmak zorunda; eşlenemeyen etiketler veri kalite kuyruğuna | 08 | D-6'nın veri hattında garantisi |
| B-28 | P1 | İzin günü çevrimi çalışma düzenine göre (5 gün → 9 sa, 6 gün → 7,5 sa) ve toplu sözleşme/sözleşmeden doğan ek izin hakkı (KPI-055 paydası) ⚖️ | 05/09 | 6 günlük şantiyelerde KPI-055 düşük görünür |
| B-29 | P1 | İş kazası/meslek hastalığı istirahatleri için ayrı izin türü (`work_injury`), KPI-053 dışında — kaza gizleme baskısını önler (ADR gerekir) | 02/09/11 | HR Director E.2 |
| B-30 | P2 | "İzin Sağlığı" paneli ve grup düzeyi uyarılar (KPI-056 eşik aşımı, kâğıt üstünde izin, işten ayrılmadan izin ücreti ödemesi) — bireysel sıralama yok | 07/14 | HR Director E.3 |
| B-17 | P2 | Dinamik rapor oluşturucu, kalan 10 rapor şablonu | 14 | FAZ 4 |
