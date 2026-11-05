# HRSWI — Proje Planı · Agent 00 (Orchestrator)

## 1. Teknik envanter (ADIM 1)
Başlangıçta çalışma dizini **boştu**; korunacak mevcut kod yoktu. Ortam: Python 3.13, Node 22, PostgreSQL 16, Redis 7, Docker 29 (daemon elle başlatıldı), Chromium/Playwright.

## 2. Varsayımlar ve belirsizlikler (ADIM 2)
| # | Varsayım / belirsizlik | Karar | Kim doğrulamalı |
|---|---|---|---|
| A1 | Gerçek kaynak sistemler (Portal X, ERP, SGK, turnike) erişilebilir değil | Bağlayıcı arayüzü + dosya importu + sahte bağlayıcı; gerçek entegrasyon yetkili API belgeleriyle FAZ 2+ | Müşteri BT |
| A2 | Devamsızlık tanımı kuruma göre değişir | KPI-003 parametreli (`absence_types`), varsayılan: sick, unexcused, unpaid | İK direktörü ⚖️ |
| A3 | "Personel devri" grup içi transferi kapsar mı? | Kapsam içi 7 gün içinde yeni istihdam = transfer (ayrılış sayılmaz); firma kapsamında sayılır | İK direktörü |
| A4 | Kart okuma verisi | Yalnızca varlık kanıtı; üretken süre `productive_hours` (iş paketi kaydı) | Operasyon |
| A5 | Para birimi | Raporlama birimi TRY; kur `fx_rates`; kur yoksa hesaplanamaz | Finans |
| A6 | Sentetik veri haftalık puantaj üretir | Üretimde günlük; formüller granülerlikten bağımsız | — |
| A7 | Standart sürümleri (ISO 30414:2025, ESRS Omnibus, TSRS) | `version_verified=false`; uyum iddiası yok | Sürdürülebilirlik ⚖️ |
| A8 | LLM kullanımı | Varsayılan kapalı; kişisel veri asla gönderilmez | Güvenlik/Hukuk |
| A9 | Formül versiyonları global | Tenant'a özel override P1 backlog | Orchestrator |

## 3. Fazlar ve bu teslimdeki gerçek durum
| Faz | Kapsam | Durum |
|---|---|---|
| FAZ 0 Analiz/Mimari | PRD, hikâyeler, KPI sözlüğü (52), veri modeli, mimari (15 ADR), yetki matrisi, ekran haritası, iş listesi, riskler | ✔ Tamamlandı |
| FAZ 1 Teknik iskelet | Repo, FastAPI, Next.js, PostgreSQL, Alembic, JWT auth, Docker Compose, CI, ilk ekran ve API | ✔ Tamamlandı (API imajı Docker Hub limiti nedeniyle bu ortamda derlenemedi, web imajı derlendi) |
| FAZ 2 İK veri altyapısı | Hiyerarşi, personel, istihdam dönemleri, import, veri kalitesi | ◐ Veri modeli + import + kalite kontrolleri ✔; CRUD/yönetim UI ✖ |
| FAZ 3 Analitik MVP | 15 KPI, motor, dashboard, filtreler | ✔ 18 aktif KPI (KPI-012 ve KPI-015 bilinçli olarak taslak/aile) |
| FAZ 4 Sürdürülebilirlik | Metrikler, dashboard, standart eşleştirme, raporlama | ◐ Dashboard + kapsam + 2 rapor ✔; kanıt yönetimi UI ✖ |
| FAZ 5 Verimlilik/maliyet | İş paketi, adam-saat, maliyet, yüklenici karşılaştırma, planlama | ◐ KPI'lar + karşılaştırma ✔; planlama/senaryo ✖ |
| FAZ 6 Yapay zekâ | Sorgu asistanı, tahmin, anomali, senaryo, özet | ◐ Asistan (deterministik), tahmin, anomali ✔; senaryo simülasyonu, LLM ✖ |
| FAZ 7 Hardening | Güvenlik, performans, i18n, yedek, izleme | ◐ Önbellek + ölçüm ✔; geri kalanı ✖ |

## 4. Orchestrator çalışma sırası (ADIM 1–12) — yapılanlar
1. Envanter ✔ · 2. Varsayımlar ✔ · 3. Ajan ekipleri ve bağımlılık planı → `AGENTS.md` ✔ · 4. PRD, mimari, veri modeli, KPI sözlüğü, ekran haritası → `docs/` ✔ · 5. API sözleşmeleri → `INTEGRATION_CONTRACTS.md` + OpenAPI ✔ · 6. Önceliklendirilmiş backlog → `TASK_BOARD.md` ✔ · 7–8. Çalışan uygulama, bileşenler bağlı ✔ · 9. Sentetik veriyle fonksiyonel + güvenlik testleri (73 backend + 12 E2E) ✔ · 10. Dashboard gerçek API ile ✔ · 11. Bağımsız denetim → `docs/AUDIT_REPORT.md` ✔ · 12. Rapor → `README.md` + `TASK_BOARD.md` ✔

## 5. Riskler
| Risk | Etki | Önlem |
|---|---|---|
| Kaynak sistem verisinin kalitesi düşük / kimlikler tutarsız | KPI güvenilirliği | Kalite puanı, kimlik inceleme kuyruğu, `unknown` kategorisi, eksik veri durumu |
| KPI tanımlarında kurum içi uzlaşı yok | Güven kaybı | Versiyonlu formüller, parametreler, KPI sözlüğü, onaylı aktivasyon |
| Alt yüklenici veri paylaşımının hukuki dayanağı | KVKK | `data_sharing_granted` + sözleşme kanıtı, denetim izi ⚖️ |
| Yapay zekânın yanlış/aşırı yorumu | Hatalı karar | Sayılar yalnızca motordan, korelasyon≠nedensellik uyarıları, yasaklı bireysel kararlar |
| 15K ölçekte performans | Kullanılabilirlik | Önbellek (ADR-015), toplu seri hesaplayıcılar, partition (backlog) |
