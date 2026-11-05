# HRSWI — Sürüm Planı ve Kalite Kapıları

## Sürümler
| Sürüm | Faz | İçerik | Durum |
|---|---|---|---|
| v0.1 "Skeleton+MVP core" | FAZ 0–1 + FAZ 2/3 çekirdeği | Mimari, veri modeli, auth/RBAC/ABAC, import, 18 KPI, dashboard, tahmin, asistan, 2 rapor, Docker/CI | **Bu teslim** — dahili demo (sentetik veri) |
| v0.2 | FAZ 2 tamamlama | Org/personel CRUD UI, kimlik inceleme kuyruğu UI, puantaj/izin/maliyet import tipleri, kullanıcı-rol yönetimi UI, Celery | Planlı |
| v0.3 | FAZ 3–4 | Kalan MVP KPI'ları (020–031 doğrulama), İSG ve eğitim ekranları, sürdürülebilirlik kanıt yönetimi, risk/aksiyon düzenleme | Planlı |
| v0.4 | FAZ 5 | İş paketi verimliliği, maliyet ekranları, planlama ve senaryo simülasyonu | Planlı |
| v0.5 | FAZ 6 | LLM destekli niyet çözümleme/anlatı (yalnızca toplu veri), açıklanabilir öneriler | Planlı |
| v1.0 | FAZ 7 | OIDC/MFA, RLS, şifreleme/KMS, saklama-imha, tam i18n, ölçek ve yük testi, yedek/geri yükleme tatbikatı, izleme | Planlı |

## Kalite kapıları (her birleştirmede — CI)
1. `ruff check` temiz · `tsc --noEmit` temiz · `eslint` hatasız
2. `alembic upgrade head` + `alembic check` (model–migration sapması yok)
3. `pytest` tamamı geçer (şu an 130 test, gerçek PostgreSQL)
4. `next build` başarılı · `docker compose build`
5. Ortak sözleşme değişikliği → ADR + Orchestrator onayı

## Yayına alma kontrol listesi (v1.0 — henüz karşılanmadı)
- [ ] Bağımsız güvenlik testi (pentest) ve `docs/AUDIT_REPORT.md` kritik/yüksek bulguları kapalı
- [ ] OIDC/MFA, httpOnly oturum, rate limit (SECURITY_PRIVACY G1–G6)
- [ ] KVKK hukuki inceleme maddeleri onaylı (⚖️)
- [ ] 15K/100K ölçek verisiyle performans ve 150 kullanıcı (düşünme süreli) yük testi raporu
- [ ] Yedekten geri yükleme tatbikatı belgelenmiş (RPO/RTO)
- [ ] İzleme panelleri: hata oranı, gecikme, import başarı oranı, servis sağlığı
- [ ] Standart sürümleri doğrulanmış (`version_verified=true`)
- [ ] Kullanıcı kılavuzu + yönetici rehberi TR/EN/RU
- [ ] Geri dönüş (rollback) prosedürü: önceki imaj + `alembic downgrade` testi

## Geri dönüş
Uygulama imajı versiyonlu; migration'lar ileri uyumlu yazılır (önce ekle → sonra kullan → sonra kaldır). Veri kaybı riski taşıyan downgrade'ler yasaktır; geri dönüş önceki imaja dönmektir.
