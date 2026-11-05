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
| T-022 | Bağımsız denetim | 16 | T-001..T-021 | Bulgu raporu, kritik/yüksek bulgular kapatılır | Done | `docs/AUDIT_REPORT.md` |

## Backlog (önceliklendirilmiş)
Değerlendirme: iş değeri / kullanıcı önemi / maliyet / bağımlılık / veri bulunabilirliği / güvenlik etkisi / operasyonel risk.

| ID | Öncelik | Görev | Ajan | Gerekçe |
|---|---|---|---|---|
| B-01 | P0 | OIDC + MFA, httpOnly oturum (BFF), rate limit | 11 | Kurumsal kullanım ön koşulu; XSS ile token çalınma riski |
| B-02 | P0 | PostgreSQL RLS (ikinci savunma hattı) | 05/11 | Yetki hatası = veri sızıntısı |
| B-03 | P0 | Saklama-imha işleri (staging ham satırları dahil) | 08/11 | KVKK md.7 |
| B-04 | P0 | 15K/100K ölçek verisiyle performans + düşünme süreli 150 kullanıcı yük testi | 13 | Hedef doğrulanmadı |
| B-05 | P0 | Yedekleme/geri yükleme tatbikatı, izleme panelleri, `/metrics` | 13 | Operasyonel süreklilik |
| B-06 | P1 | Org/personel/istihdam CRUD UI + kimlik inceleme kuyruğu UI | 06/07 | FAZ 2 tamamlama |
| B-07 | P1 | Puantaj, izin, maliyet, eğitim, İSG import tipleri + bağlayıcı çerçevesi | 08 | Gerçek veri girişi |
| B-08 | P1 | Kullanıcı/rol atama UI | 07/11 | Yönetim |
| B-09 | P1 | Tenant'a özel formül parametreleri | 09 | Kurumlar arası farklı tanımlar |
| B-10 | P1 | Taslak KPI'ların (020–051) doğrulanıp aktive edilmesi | 02/09 | MVP iş değeri |
| B-11 | P1 | Risk/aksiyon oluşturma-düzenleme, kanıt yükleme | 06/07 | Modül K/M |
| B-12 | P1 | Celery: asenkron rapor/import/hesaplama, planlanmış raporlar | 13/14 | Ağır iş yükleri |
| B-13 | P1 | Toplu aylık seri hesaplayıcıları (tek SQL) | 09 | Soğuk önbellek performansı |
| B-14 | P2 | LLM ile niyet çözümleme/anlatı (yalnızca toplu veri) | 10 | Kullanılabilirlik |
| B-15 | P2 | Senaryo simülasyonu, iş paketi bazlı planlama | 04/10 | FAZ 5–6 |
| B-16 | P2 | Çevresel etki modülü (KPI-052) | 03 | Opsiyonel modül N |
| B-17 | P2 | Dinamik rapor oluşturucu, kalan 10 rapor şablonu | 14 | FAZ 4 |
