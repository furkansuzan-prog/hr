# HRSWI — Performans Ölçüm Raporu (geliştirme ortamı) · Agent 13

> Bu değerler **geliştirme makinesinde** alınmıştır; üretim kapasitesi iddiası değildir. 15K aktif personel ölçek testi 2026-11-05'te yapıldı (aşağıda **§ 15K veri seti**): sıcak önbellekte 150 düşünme süreli kullanıcı hedefi **geçti**, soğuk önbellek / doygunluk senaryoları **kaldı**.

## İlk ölçümler (2,553 kişilik demo veri seti)

## Ortam
- 4 vCPU konteyner, PostgreSQL 16 ve Redis 7 aynı makinede; API: `uvicorn --workers 4`, `HRSWI_REDIS_URL` açık.
- Veri: sentetik demo — 2,553 kişi, 2,605 istihdam, 145,770 haftalık puantaj, 177,602 maliyet satırı, 14 tüzel kişilik, 28 ay.
- Araç: `backend/scripts/perf_smoke.py` (kapalı döngü, düşünme süresi yok, 6 farklı kullanıcı/kapsam, ağırlıklı uç karışımı).

## Sonuçlar (ms)
| Senaryo | Dashboard p50 / p95 | KPI evaluate p95 | Seri p95 | Firma kırılımı p95 | Personel listesi p95 | Hata |
|---|---|---|---|---|---|---|
| Önbelleksiz (ilk ölçüm), 30 eşzamanlı | 7,497 / 9,844 | 724 | 2,138 | 1,547 | 742 | 0 |
| Redis önbellek + istek içi memo, 30 eşzamanlı, soğuk | 454 / 2,393 | 882 | 1,059 | 795 | 1,359 | 0 |
| aynı, **sıcak** | 696 / **1,064** ✔ | 276 | 367 | 480 | 333 | 0 |
| aynı, sıcak, **150 eşzamanlı** | 2,241 / **3,602** ✖ | 2,710 | 2,570 | 2,816 | 4,081 | 0 |

Tek kullanıcı, önbelleksiz yönetici dashboard'u: ~1.5 sn. 5,000 satırlık XLSX içe aktarma (yükle + doğrula + commit): test paketinde ölçülür (`test_large_excel_import`, eşik 120 sn).

## Yorum
- Hedef p95 < 2 sn: 30 eşzamanlı istekte sağlanıyor; 150 eşzamanlı **düşünme süresiz** istekte sağlanmıyor (4 vCPU'da ~40 istek/sn doygunluk). Gerçek 150 kullanıcı düşünme süresiyle çok daha düşük istek hızı üretir; bu ayrıca ölçülmelidir (k6/Locust, düşünme süreli senaryo).
- İlk ölçümde 150 eşzamanlıda bağlantı havuzu tükenmesi (4 worker × 30 > `max_connections=100`) hataya yol açıyordu; havuz boyutu yapılandırılabilir yapıldı (`HRSWI_DB_POOL_SIZE`/`MAX_OVERFLOW`), üretimde PgBouncer önerilir.

## Sonraki optimizasyonlar (backlog)
1. Aylık serileri tek SQL'de (GROUP BY ay) hesaplayan toplu hesaplayıcılar (soğuk seri maliyeti ÷12).
2. `AccessContext`'in token başına kısa süreli önbelleklenmesi (her istekte 3–5 sorgu).
3. `timesheets` aylık özet tablosu / materyalize görünüm ve range partition.
4. 15K aktif / 100K tarihsel sentetik veriyle ölçek testi (`--employees 15000`).
5. Ağır raporların Celery ile asenkron üretimi.


---

## 15K veri seti — ölçek testi (B-04, 2026-11-05, A13)

### Ortam ve yöntem
- Aynı 4 vCPU / 15 GB konteyner; PostgreSQL 16 (varsayılan ayarlar: `shared_buffers=128MB`, `work_mem=4MB`, `max_connections=100`, `pg_stat_statements` yüklü değil — sunucu paylaşımlı olduğu için yeniden başlatılmadı) ve Redis 7 aynı makinede.
- API: `uvicorn app.main:app --workers 4` (port 8200), `HRSWI_REDIS_URL=redis://localhost:6379/1`, havuz varsayılanı 8+4 bağlantı/worker. Kod: 12:52'deki çalışma ağacı (migration `0004`; diğer ajanların o ana kadarki değişiklikleri, ör. toplu seri hesaplayıcı, dahil).
- **CPU paylaşımı:** makine diğer ajanlarla paylaşıldı (pytest, `next build`, `tsc` aynı anda çalıştı; ölçüm sırasında 1 dk yük ortalaması 2–22). Yük oluşturucu da aynı makinede. Sayılar bu nedenle gürültülüdür; 30 VU sıcak ölçümü iki kez tekrarlandı (aralık verilmiştir). Container yeniden başlamadan önceki ilk tur (09:36–09:41) da benzer sonuç verdi (sıcak 30 VU dashboard p95 4.4 sn, soğuk 83 sn); o turun 150 VU koşusu yeniden başlatma yüzünden bağlantı hatalarıyla bozulduğu için **kullanılmadı**.
- Araç: `backend/scripts/perf_smoke.py` — yeni `--think-time` (üstel dağılım, ortalama sn), `--duration`, `--ramp-up`, `--seed`, `--json`; p50/p95/p99/max ve durum kodu dağılımı. Kapalı döngü sanal kullanıcı (VU); 6 demo kullanıcının token'ı VU'lara dağıtılır; uç karışımı öncekiyle aynı (403'ler kapsam dışı kullanıcı beklenen yanıtıdır, hata sayılmaz).
- "Soğuk" = Redis KPI önbelleği boş (`FLUSHDB`); PostgreSQL/OS sayfa önbelleği ılık. Gerçek hayatta her import commit'i (`data_version` artışı) tenant'ın tüm önbelleğini geçersizleştirdiği için **soğuk senaryo her veri yüklemesinden sonra yaşanır.**

### Veri üretimi
`python -m app.seed.synthetic --employees 15000 --subcontractors 200 --months 28` (hrswi_scale):

| Ölçü | Değer |
|---|---|
| Süre / tepe bellek (RSS) | **234 sn** / **1.40 GB** |
| Kişi / istihdam (aktif) | 30,599 / 31,229 (**15,294 aktif**) |
| Tüzel kişilik | 202 (2 ana + 200 taşeron) |
| Haftalık puantaj / maliyet satırı | 1,831,878 / 2,227,149 |
| İzin / eğitim / iş güvenliği / üretim | 148,630 / 60,898 / 10,626 / 12,673 |
| Toplam satır (57 tablo) | 4,462,490 |
| Veritabanı boyutu | **770 MB** (timesheets 358 MB, payroll_costs 330 MB) |

Üretici kabul edilebilir hızda; bellek tepe noktası tüm puantaj/maliyet satırlarının COPY öncesi Python listelerinde tutulmasından (öneri: tablo başına akışlı/parçalı COPY — `app/seed/synthetic.py` sahibine not; dosya değiştirilmedi). Üretimde günlük puantaj ≈ 6× satır (≈ 11M/28 ay) demektir.

### Sonuçlar (ms)
| Senaryo | İstek | Verim (istek/sn) | Dashboard p50 / p95 | KPI evaluate p95 | Seri p95 | Firma kırılımı p95 | Personel listesi p95 | Tümü p95 | Hata |
|---|---|---|---|---|---|---|---|---|---|
| Tek kullanıcı, önbelleksiz dashboard | 1 | — | **19,840** (372 SQL, 19.6 sn SQL) | 270 | 2,470 | 330 | — | — | 0 |
| (a) 30 VU, düşünme yok, **soğuk** | 600 | 6.4 | 1,776 / **85,704** ✖ | 8,214 | 36,459 | 6,447 | 7,481 | 30,255 | **7×500** (havuz zaman aşımı) |
| (a) 30 VU, düşünme yok, **sıcak** (2 tekrar) | 2×600 | 29.2 – 37.2 | 1,729–1,858 / **3,484–4,498** ✖ | 420–570 | 826–928 | 2,389–3,215 | 450–500 | 2,784–3,681 | 0 |
| (b) **150 VU, düşünme ~5 sn, sıcak**, 180 sn | 5,294 | 29.4 | 213 / **512** ✔ | 86 | 143 | 344 | 118 | **335** | 1 ReadError (0.02%) |
| (b) 150 VU, düşünme ~5 sn, **soğuk başlangıç**, 180 sn | 2,135 | 11.8 | 442 / **117,458** ✖ | 30,977 | 31,658 | 32,825 | 41,389 | 33,523 | **172×500** (havuz zaman aşımı) |

Tek kullanıcı sıcak dashboard (süreç içi profil): ~150 ms, 42 SQL (21 ms), 262 KPI değerlendirmesi önbellekten.

### Hedef değerlendirmesi
| Hedef | Sonuç |
|---|---|
| 15K aktif personel veri seti üretilebilir | ✔ (234 sn, 1.4 GB RAM) |
| 150 eşzamanlı kullanıcı (gerçekçi, ~5 sn düşünme), **sıcak önbellek**: dashboard p95 < 2 sn | ✔ **512 ms**, hata %0.02 |
| 150 kullanıcı, **önbellek geçersizleştirmesi sonrası** (import ertesi) p95 < 2 sn | ✖ 117 sn; %8 istek 500 (bağlantı havuzu zaman aşımı) |
| 30 eşzamanlı düşünme süresiz (doygunluk) dashboard p95 < 2 sn | ✖ 3.5–4.5 sn (2.5K veride ✔ 1.06 sn idi); doygunluk ~30–37 istek/sn |
| Tek kullanıcı soğuk dashboard | ✖ 19.8 sn (2.5K veride ~1.5 sn) |

Kapasite notu: 150 VU × (1/~5.1 sn) ≈ 29 istek/sn talep, ölçülen sıcak doygunluk (~30–37 istek/sn, paylaşımlı 4 vCPU) ile **neredeyse aynı** — gerçekçi yükte yedek kapasite yok denecek kadar az. Üretimde API'yi DB'den ayrı makinede ≥ 8 vCPU ile koşturmak ve yatay ölçeklemek gerekir.

### Darboğaz analizi (soğuk yol)
Soğuk dashboard'un 19.6 sn'lik SQL süresinin %90'ı üç sorgu ailesinden gelir (SQLAlchemy olay kancalı süreç içi profil; `pg_stat_statements` yok):

| Sorgu (hesaplayıcı) | Çağrı | Toplam | Tek çağrı EXPLAIN ANALYZE |
|---|---|---|---|
| Q1 puantaj pencere toplamı (`timesheets ⋈ employment_relationships`, `count(DISTINCT person_id)`) | 17 | 13.6 sn | 1 ay: **334 ms** (bitmap heap scan, 15,170 sayfa okuma, sıralama diske taşıyor 3.2 MB); 12 aylık seri: **2,905 ms** (paralel seq scan 1.83M satır, 3×~8 MB disk sıralaması) |
| Q2 maliyet (para birimi × ay × tür, `count(DISTINCT person_id)`) | 1 | 3.5 sn | **4,263 ms** — 967K satırın `count(DISTINCT)` için sıralanması (external merge 37 MB) |
| Q3 tarihte personel sayısı (`unnest(dates) ⋈ employment`) | 18 + 202 | 1.4 sn | 32 ms/çağrı; firma kırılımında 202 çağrı |
| Q4 izin kesişimi | 13 | 0.6 sn | 36 ms |

Tam planlar: deney sırasında alınan çıktılar bu raporun özetidir (Buffers/zamanlar yukarıda). Deneyler `hrswi_scale` üzerinde yapıldı, deney nesneleri sonra silindi (şema migration `0004` ile aynı kaldı).

| Deney | Sonuç | Karar |
|---|---|---|
| `work_mem=64MB` (oturum) | Q1 334→292 ms, Q1-12ay 2,905→2,736 ms, Q2 değişmedi (4.5 sn) | Küçük kazanç; API rolü için 16–32 MB önerilir |
| İki seviyeli toplama (önce kişi bazında GROUP BY) | Q2 3.4–4.5 sn, Q1-12ay 2.6–2.9 sn | Fayda yok |
| **Kapsayan indeks** `timesheets (work_date) INCLUDE (...)` (118 MB) | Q1: Index Only Scan, Heap Fetches 0, okuma 15,170 sayfa → 984 hit; 334→275 ms; Q1-12ay (+32 MB work_mem) 2,905→2,060 ms | Orta öncelik (I/O azaltır; sıralama maliyeti kalır) |
| **Aylık özet tablo** `timesheet_monthly (tenant, employment, ay, status, toplamlar)` (446K satır, 91 MB; indeks INCLUDE) | Q1 1 ay **334→35 ms (9.5×)**; 12 ay **2,905→525 ms (5.5×)** | **Yüksek öncelik** |
| Q2'den `count(DISTINCT person)` çıkarılması | **4,263→309 ms (14×)**; ayrı "ay×para birimi distinct istihdam" sorgusu 299 ms | **Yüksek öncelik** (hesaplayıcı değişikliği) |

### Önerilen DDL / değişiklikler (migration'ı Data Architect / KPI ajanı yazmalı — A13 migration oluşturmadı)
```sql
-- 1) Aylık puantaj özeti (import commit'inde, mark_stale_from ile aynı transaction'da etkilenen (employment, ay) için yeniden hesaplanır)
CREATE TABLE timesheet_monthly (
  tenant_id int NOT NULL, employment_id int NOT NULL REFERENCES employment_relationships(id),
  month date NOT NULL, status varchar(20) NOT NULL,
  n int NOT NULL, planned numeric, worked numeric, overtime numeric, productive numeric, n_productive int,
  planned_productive numeric, night numeric,
  PRIMARY KEY (tenant_id, month, employment_id, status));
CREATE INDEX ix_tsm_tenant_month_cover ON timesheet_monthly (tenant_id, month)
  INCLUDE (employment_id, status, n, planned, worked, overtime, productive, n_productive, planned_productive, night);
-- Not: haftalık/günlük satırın ay sınırını aşması durumunda (sentetik veri haftalık, work_date = Pazartesi) özet, Q1'in
-- "work_date BETWEEN ay başı/sonu" semantiğiyle birebir aynıdır; keyfi tarih aralıkları (ay dışı) ham tabloya düşmeli.

-- 2) Ham tabloda kapsayan indeks (ay dışı aralıklar ve ad-hoc sorgular için)
CREATE INDEX CONCURRENTLY ix_ts_tenant_date_cover ON timesheets (tenant_id, work_date)
  INCLUDE (employment_id, status, planned_hours, worked_hours, overtime_hours, productive_hours, night_hours);
-- hesaplayıcılar t.tenant_id = :tenant (ve pc.tenant_id, l.tenant_id) koşulunu da eklemeli; şu an yalnızca
-- e.tenant_id filtreleniyor, bu yüzden tenant öncelikli indeksler çok kiracılı veride seçici kullanılamaz.

-- 3) Bölümleme: günlük puantaj akışı geldiğinde (≈ 6× satır) timesheets'i work_date üzerinden RANGE (çeyreklik) bölümle.
--    uq_timesheet_day zaten work_date içeriyor; PK (id, work_date) olmalı. 1.8M satırda henüz gerekmiyor.
-- 4) PostgreSQL: shared_buffers ≈ RAM'in %25'i (770 MB veri 128 MB'a sığmıyor), API rolü için work_mem 16–32 MB,
--    shared_preload_libraries = 'pg_stat_statements' (üretimde sorgu izleme için zorunlu).
```

### Uygulama düzeyi öneriler (en büyük etki)
1. **Önbellek "dogpile" koruması:** soğuk anahtar için tek hesaplayıcı (Redis `SET key:lock NX EX 30`), diğer istekler kısa bekleyip önbelleği okur. Soğuk 150 VU'da aynı ~75 KPI'yı yüzlerce istek paralel hesaplıyor.
2. **Ön ısıtma:** `data_version` artışından sonra (import commit) arka plan işi yönetici/İK dashboard kapsamlarını önceden hesaplasın (Celery/arq). Kullanıcı soğuk yolu görmemeli.
3. **Havuz ↔ thread havuzu uyumu:** senkron uçlar worker başına 40 anyio thread'inde koşuyor, DB havuzu 12 → yük altında `QueuePool limit ... timed out` → 500 (soğuk testlerde 7 ve 172 adet). Ya anyio limiter'ı `pool_size+max_overflow`'a eşitleyin ya da PgBouncer ile havuzu büyütün; havuz zaman aşımında 500 yerine 503 + `Retry-After` dönün.
4. Firma kırılımı (202 tüzel kişilik) sıcakken bile 30 VU'da p95 2.4–3.2 sn: yanıt 170 KB, 202 ayrı önbellek okuması → Redis `MGET` / yanıt düzeyinde önbellek ve sayfalama.
5. Q2 için `count(DISTINCT)`'i ayrı, ucuz sorguya bölmek (yukarıda 14×) ve aylık seri hesaplayıcılarının özet tabloya geçmesi.

### Tekrar üretme
```bash
cd backend
export HRSWI_DATABASE_URL=postgresql+psycopg://hrswi:hrswi@localhost:5432/hrswi_scale
alembic upgrade head && python -m app.seed.synthetic --employees 15000 --subcontractors 200 --months 28
HRSWI_REDIS_URL=redis://localhost:6379/1 uvicorn app.main:app --port 8200 --workers 4 &
redis-cli -n 1 flushdb   # soğuk
python scripts/perf_smoke.py --base http://localhost:8200 --concurrency 30 --requests 600 --json a.json
python scripts/perf_smoke.py --base http://localhost:8200 --concurrency 150 --think-time 5 --duration 180 --requests 0 --json b.json
```
