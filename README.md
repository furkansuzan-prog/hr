# HRSWI — HR Sustainability & Workforce Intelligence

Çok şirketli, çok projeli, yapay zekâ destekli İK sürdürülebilirlik, iş gücü verimliliği ve karar destek platformu.
**Durum: v0.1 — çalışan iskelet + MVP çekirdeği, yalnızca sentetik demo verisi.** Üretime hazır değildir (bkz. `RELEASE_PLAN.md`).

| Belge | İçerik |
|---|---|
| [PROJECT_PLAN.md](PROJECT_PLAN.md) | Envanter, varsayımlar, faz durumu, riskler |
| [AGENTS.md](AGENTS.md) | Ajan rolleri, çalışma alanları, bağımlılık planı, protokol |
| [TASK_BOARD.md](TASK_BOARD.md) | Görev ID'leri, sahipler, durum, kabul kriterleri, test kanıtı, backlog (P0/P1/P2) |
| [ARCHITECTURE_DECISIONS.md](ARCHITECTURE_DECISIONS.md) | 15 ADR |
| [INTEGRATION_CONTRACTS.md](INTEGRATION_CONTRACTS.md) | API değişmezleri, `KpiResult`, yetki ve yazma kuralları |
| [RELEASE_PLAN.md](RELEASE_PLAN.md) | Sürümler, kalite kapıları, yayın kontrol listesi |
| [docs/PRD.md](docs/PRD.md) · [SCREEN_MAP](docs/SCREEN_MAP.md) | Ürün gereksinimleri, hikâyeler, ekranlar |
| [docs/KPI_CATALOG.md](docs/KPI_CATALOG.md) | 52 KPI tanımı (koddan üretilir) |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) · [DATA_DICTIONARY](docs/DATA_DICTIONARY.md) | ER modeli, 58 tablo |
| [docs/SECURITY_PRIVACY.md](docs/SECURITY_PRIVACY.md) · [PERMISSION_MATRIX](docs/PERMISSION_MATRIX.md) | KVKK kontrolleri, boşluklar, hukuki inceleme maddeleri |
| [docs/PERFORMANCE_REPORT.md](docs/PERFORMANCE_REPORT.md) | Ölçülmüş gecikmeler (geliştirme ortamı) |
| [docs/AUDIT_REPORT.md](docs/AUDIT_REPORT.md) | Bağımsız denetim bulguları ve çözüm durumu |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Ürün kararları (görünürlük, dönem kapatma, devamsızlık tanımı, KPI hedefleri) |
| [docs/USAGE_POLICY.md](docs/USAGE_POLICY.md) | Kullanım politikası (D-14): çıktılar toplu karar desteğidir; ücret, prim, terfi, disiplin, bireysel değerlendirmede kullanılmaz |
| [docs/KPI_TARGETS_REQUEST.md](docs/KPI_TARGETS_REQUEST.md) | İK yöneticisi için KPI hedef/eşik formu |

## Mimari özet
Modüler monolit (ADR-001): **FastAPI** (`backend/app`: `api` → `services` → `analytics`/`ai` → `models`, çapraz `security`) + **PostgreSQL 16** + **Redis** (KPI önbelleği) + **Next.js 16 / React 19 / Tailwind 4 / ECharts / TanStack Query & Table** (`frontend/src`).
Tüm sayılar versiyonlu KPI motorundan gelir (`analytics/engine.py`); her istekte tenant ve tüzel kişilik kapsamı uygulanır (`security/access.py`).

## Hızlı başlangıç (yerel)
Gereksinimler: Python 3.12+, Node 22, PostgreSQL 16, (opsiyonel) Redis.

```bash
createdb hrswi
```
```bash
cd backend && pip install -r requirements-dev.txt
```
```bash
cd backend && alembic upgrade head && python -m app.seed.synthetic --employees 1200
```
```bash
cd backend && HRSWI_REDIS_URL=redis://localhost:6379/0 uvicorn app.main:app --port 8000 --workers 2
```
```bash
cd frontend && npm ci && npm run build && npx next start -p 3000
```
Arayüz: http://localhost:3000 · API belgeleri: http://localhost:8000/docs

Demo kullanıcılar (parola `Demo!2345`, `HRSWI_DEMO_PASSWORD` ile değiştirilebilir): `admin@demo.local`, `hr.director@demo.local`, `hr.manager@demo.local`, `finance@demo.local`, `exec@demo.local`, `sustainability@demo.local`, `hse@demo.local`, `auditor@demo.local`, `mc.ops@demo.local` (ana yükleniciye kapsamlı), `sub01.manager@demo.local` / `sub02.manager@demo.local` (kendi firmasına kapsamlı).

## Docker
```bash
docker compose up -d db redis api web
```
```bash
docker compose --profile seed run --rm seed
```
TLS denetimli kurumsal proxy arkasında derlerken: `docker build --secret id=extra_ca,src=corp-ca.crt ...` (sertifika yoksa adım etkisizdir).

## Testler
```bash
cd backend && createdb hrswi_test && pytest -q
```
130 test: KPI'ların el hesabıyla doğrulanması, 20 zorunlu senaryo, yetki izolasyonu, import (5.000 satır XLSX dahil), asistan, raporlar ve bağımsız denetim bulgularının regresyon testleri. Testler gerçek PostgreSQL kullanır ve şemayı migration'larla kurar.
```bash
cd frontend && node e2e/smoke.mjs ../docs/screenshots
```
Çalışan sisteme karşı 29 kontrollü Playwright uçtan uca testi (giriş, dashboard, KPI detayı, asistan, kapsam izolasyonu, dil, tema, mobil, konsol hataları).
```bash
cd backend && python scripts/perf_smoke.py --concurrency 30 --requests 600
```

## Ortam değişkenleri
| Değişken | Varsayılan | Not |
|---|---|---|
| `HRSWI_ENV` | development | `production` iken varsayılan sırlarla başlamaz |
| `HRSWI_DATABASE_URL` | postgresql+psycopg://hrswi:hrswi@localhost:5432/hrswi | |
| `HRSWI_JWT_SECRET` | dev değeri | Üretimde ≥32 karakter, sır yöneticisinden |
| `HRSWI_IDENTITY_HMAC_KEY` | dev değeri | TC kimlik HMAC anahtarı; değişirse eşleştirme bozulur |
| `HRSWI_REDIS_URL` | — | Ayarlıysa KPI önbelleği worker'lar arası paylaşılır |
| `HRSWI_MIN_GROUP_SIZE` | 5 | Küçük örneklem bastırma eşiği |
| `HRSWI_FORECAST_MIN_POINTS` | 12 | Tahmin için asgari aylık gözlem |
| `HRSWI_DB_POOL_SIZE` / `HRSWI_DB_MAX_OVERFLOW` | 8 / 4 | Worker başına |
| `HRSWI_LLM_ENABLED` | false | FAZ 6 |
| `HRSWI_COVERAGE_MODES` | `default=warn,safety=enforce` | Besleme kapsam kapısı (ADR-020, karar D-13): İSG beyanı eksik/yetersizse KPI-014A/B/043 `insufficient_data`; diğer beslemeler uyarı (renksiz değer). Besleme başına `off \| warn \| enforce` |
| `BACKEND_URL` | http://localhost:8000 | Frontend derleme zamanı: tarayıcı aynı adresten `/api/*` çağırır, Next bu adrese iletir |
| `NEXT_PUBLIC_API_URL` | (boş) | Yalnızca API başka bir origin'de çağrılacaksa |

## Depo yapısı
```
backend/app/{core,models,security,analytics,ai,services,api,seed}   backend/alembic  backend/tests  backend/scripts
frontend/src/{app,components,lib}   frontend/e2e
docs/   .github/workflows/ci.yml   docker-compose.yml
```
