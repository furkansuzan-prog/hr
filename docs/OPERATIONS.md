# HRSWI — İşletim Kılavuzu (Operations) · Agent 13

Kapsam: yedekleme/geri yükleme, RPO/RTO önerisi, izleme ve alarmlar (`/metrics`), dağıtım ve geri alma, log yönetimi.
Ölçümler geliştirme konteynerinde (4 vCPU paylaşımlı, PostgreSQL 16 aynı makinede) alınmıştır; üretim donanımında yeniden doğrulanmalıdır.

---

## 1. Yedekleme ve geri yükleme

### Araçlar
| Betik | Ne yapar |
|---|---|
| `backend/scripts/backup.sh` | `pg_dump -Fc` (sıkıştırılmış özel format), zaman damgalı dosya `<db>_<UTC>.dump`, `.dump.sha256` sağlama toplamı, `.manifest` (tüm tabloların satır sayısı). Satır sayıları **pg_dump ile aynı anlık görüntüde** alınır (`pg_export_snapshot` + `--snapshot`) → uygulama yazarken bile manifest dump ile birebir tutarlıdır. Saklama süresi `--retention-days` (vars. 14). |
| `backend/scripts/restore_verify.sh` | Sağlama toplamını doğrular → dump'ı **yeni geçici bir veritabanına** `pg_restore --jobs N` ile yükler → her tablonun satır sayısını manifest ile (ve `--live` ile kaynak DB ile) karşılaştırır → Alembic revizyonunu karşılaştırır → geçici DB'yi **her durumda** (trap) siler. Yalnızca her şey eşleşirse çıkış kodu 0. |

```bash
cd backend
scripts/backup.sh --url postgresql://hrswi@db-host:5432/hrswi --dir /backups        # parola: ~/.pgpass / PGPASSWORD
scripts/restore_verify.sh /backups/hrswi_20261105T130451Z.dump --url postgresql://hrswi@db-host:5432/hrswi --jobs 4 --live
```

### Tatbikat sonucu (2026-11-05, `hrswi_scale`, 15K aktif personel veri seti)
| Adım | Süre | Boyut / sonuç |
|---|---|---|
| `backup.sh` (pg_dump -Fc + manifest + sha256) | **9.9 sn** dump, 10.6 sn toplam | dump **44.1 MB** (DB 808 MB → ~18× sıkıştırma), 57 tablo, snapshot `00000003-000003CA-1` |
| Sağlama toplamı doğrulama | 0.2 sn | OK |
| `pg_restore --jobs 2` + `ANALYZE` (geçici DB) | **18.6 sn** | geri yüklenen DB 773 MB |
| Satır sayısı doğrulama | 0.7 sn | **57/57 tablo, 4,462,490 satır manifest ile ve canlı kaynak ile eşleşti**; Alembic `0004` = `0004` |
| Toplam geri yükleme + doğrulama | **19.5 sn** | **RESTORE VERIFY: PASS**, geçici DB silindi |

Negatif testler (aynı gün): dump'ta 1 bayt değiştirildi → `CHECKSUM MISMATCH`, çıkış 1; manifest'te `tenants` satır sayısı değiştirildi → `MISMATCH ... RESTORE VERIFY: FAIL`, çıkış 1, geçici DB yine silindi.

CI: `.github/workflows/ci.yml` → `perf-smoke` işi (haftalık + elle) küçük veri setinde aynı tatbikatı çalıştırır (GitHub Actions üzerinde bu oturumda **çalıştırılmadı**; yerelde aynı komutlar çalıştırıldı).

### RPO / RTO önerisi
| Katman | Öneri | Gerekçe |
|---|---|---|
| **RPO ≤ 5 dk** | Yönetilen PostgreSQL'de sürekli WAL arşivleme / PITR (ör. pgBackRest, WAL-G veya bulut sağlayıcının PITR'ı) + günlük `backup.sh` mantıksal yedeği (farklı hesap/bölgede, şifreli nesne deposu, 14 gün günlük + 12 ay aylık) | İK verisi çoğunlukla toplu import ile gelir; import kaynak dosyaları (`import_batches`) ayrıca saklanırsa son import yeniden oynatılabilir. Mantıksal dump tek başına RPO = 24 saat verir. |
| **RTO ≤ 1 saat** | Ölçülen geri yükleme 15K veri setinde 19.5 sn (770 MB). Günlük puantajla ~6× veri (~5 GB) için doğrusal tahmin ~2–3 dk + uygulama dağıtımı + DNS/bağlantı değişikliği + doğrulama. 1 saat; insan karar süresi dahil gerçekçi. | Ölçüm geliştirme diskinde; üretimde çeyreklik tatbikatla doğrulanmalı. |
| Tatbikat | Ayda bir `restore_verify.sh` (otomatik, sonucu alarm sistemine), çeyrekte bir tam DR tatbikatı (PITR ile belirli bir ana dönüş) | "Test edilmemiş yedek yedek değildir." |
| Redis | Yedeklenmez | Yalnızca türetilmiş KPI önbelleği (ADR-015); kaybı yalnızca soğuk önbellek demektir (bkz. Performans raporu: soğuk yol pahalı → ön ısıtma önerisi). |
| Gizlilik | Dump'lar kişisel veri içerir (KVKK): şifreli depolama (SSE-KMS), erişim yalnızca DBA rolü, saklama süresi sonunda silme, geri yükleme tatbikatı geçici DB'yi siler. Kimlik numaraları zaten HMAC'li saklanır; `HRSWI_IDENTITY_HMAC_KEY` yedekten **ayrı** gizli yöneticide tutulmalı. |

---

## 2. İzleme: `/metrics` (B-05)

`GET /metrics` — Prometheus metin formatı (0.0.4), ek bağımlılık yok (`app/core/metrics.py`).

| Metrik | Tür | Etiketler |
|---|---|---|
| `http_requests_total` | counter | `method`, `route` (**rota şablonu**, ör. `/api/v1/kpi/evaluate/{code}`; eşleşmeyen yollar `<unmatched>`), `status` |
| `http_request_duration_seconds` | histogram (5 ms … 30 sn) | `method`, `route` |
| `http_requests_in_progress` | gauge | — |
| `kpi_cache_hits_total`, `kpi_cache_misses_total`, `kpi_cache_errors_total` | counter | — |
| `process_start_time_seconds` | gauge | — |
| `hrswi_worker_info` | gauge (1) | `pid` |

- **Kişisel veri / tenant adı yok:** etiketler beyaz listeyle sınırlı (`method/route/status/le/pid`); ham yol, id, e-posta, tenant kodu asla etiket olmaz (testle doğrulanır: `tests/test_metrics.py`).
- **Koruma:** `HRSWI_METRICS_TOKEN` ayarlıysa `Authorization: Bearer <token>` zorunlu (sabit zamanlı karşılaştırma), yoksa 401. Üretimde token ayarlayın **ve** `/metrics`'i yalnızca iç ağdan erişilebilir yapın (ingress'te dışarı kapatın). OpenAPI şemasında görünmez.
- **Çoklu worker uyarısı:** sayaçlar worker süreci başınadır. `uvicorn --workers 4` ile her kazıma rastgele bir worker'a düşer (doğrulandı: art arda 3 kazımada 3 farklı `pid`, farklı sayaç değerleri). Seçenekler: (a) **önerilen** — Kubernetes'te pod başına 1 worker (`WEB_CONCURRENCY=1`) ve pod sayısıyla ölçekleme, Prometheus her pod'u ayrı hedef olarak kazır; (b) worker başına ayrı port/sidecar; (c) `prometheus_client` multiprocess modu (`PROMETHEUS_MULTIPROC_DIR`) — yeni bağımlılık gerektirir. `rate()`/`increase()` sayaç sıfırlamalarını tolere eder ama tek hedef arkasında birden çok worker toplam değeri **yanlış** gösterir.

Prometheus kazıma örneği:
```yaml
scrape_configs:
  - job_name: hrswi-api
    metrics_path: /metrics
    authorization: { type: Bearer, credentials_file: /etc/prometheus/hrswi_metrics_token }
    kubernetes_sd_configs: [{ role: pod }]
    relabel_configs:
      - { source_labels: [__meta_kubernetes_pod_label_app], regex: hrswi-api, action: keep }
```

### Alarm önerileri
| Alarm | PromQL (özet) | Eşik / süre | Önem |
|---|---|---|---|
| API kapalı | `up{job="hrswi-api"} == 0` | 2 dk | kritik |
| 5xx oranı | `sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))` | > %2, 10 dk | kritik |
| Dashboard gecikmesi (SLO p95 < 2 sn) | `histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{route="/api/v1/dashboard/executive"}[10m])))` | > 2 sn, 15 dk | uyarı |
| Genel p95 | aynı, tüm rotalar | > 3 sn, 15 dk | uyarı |
| KPI önbellek isabet oranı düşük | `rate(kpi_cache_hits_total[15m]) / (rate(kpi_cache_hits_total[15m]) + rate(kpi_cache_misses_total[15m]))` | < %80, 30 dk (import sonrası 15 dk hariç) | uyarı |
| Önbellek arka ucu hatası | `rate(kpi_cache_errors_total[5m]) > 0` | 10 dk | uyarı (Redis) |
| Doygunluk | `sum(http_requests_in_progress)` | > 30/worker, 5 dk | uyarı |
| Sık yeniden başlama | `changes(process_start_time_seconds[1h])` | > 3 | uyarı |
| Kimlik doğrulama saldırısı | `sum(rate(http_requests_total{route="/api/v1/auth/login",status=~"401\|429"}[5m]))` | taban çizgisinin 5× | güvenlik |

Altyapı (uygulama dışı, exporter ile): PostgreSQL (`postgres_exporter`: bağlantı sayısı / `max_connections`, replikasyon gecikmesi, uzun transaction, disk), Redis (bellek, eviction), son başarılı yedek yaşı (> 26 saat → kritik), son `restore_verify` sonucu.

---

## 3. Dağıtım ve geri alma

İlkeler:
1. **Migration'lar ileri uyumlu (expand → migrate → contract).** Bir sürümün migration'ı, **bir önceki** uygulama sürümünün çalışmaya devam edebileceği şekilde yazılır: yeni sütunlar nullable ya da `server_default`'lu (örn. `0004`: `failed_login_count DEFAULT 0`, `token_version DEFAULT 0`, `locked_until NULL`); sütun/tablo silme ve yeniden adlandırma en erken **bir sonraki** sürümde, kod artık kullanmadığında yapılır. Büyük tablolarda indeks `CREATE INDEX CONCURRENTLY` ile (Alembic'te `autocommit_block`).
2. Konteyner açılışında `alembic upgrade head` çalışır (Dockerfile CMD); başarısız migration konteyneri durdurur — yarım şemalı API ayağa kalkmaz. Çok replikalı ortamda migration'ı ayrı bir **tek seferlik iş** (Kubernetes Job / `docker compose run --rm api alembic upgrade head`) olarak dağıtımdan önce çalıştırın; replikalar yarışmasın.
3. Sağlık kontrolleri: `GET /health/live` (süreç), `GET /health/ready` (DB + migration revizyonu). Readiness başarısızken trafik verilmez.

Prosedür:
```text
0. Ön koşul: CI yeşil (ruff, alembic check, pytest, frontend build, docker build).
1. backup.sh  (dağıtım öncesi mantıksal yedek; PITR varsa geri dönüş noktası zamanını not et)
2. Migration işi: alembic upgrade head   (ileri uyumlu → eski pod'lar çalışmaya devam eder)
3. Rolling update (maxUnavailable=0), readiness /health/ready, yeni pod'larda duman testi:
   perf_smoke.py --concurrency 5 --requests 50 ; /metrics'te 5xx oranı ve p95'i 15 dk izle
4. Başarılı → bitti.  Sorun →
   R1 (varsayılan) Uygulama geri alma: önceki imaj etiketine rollout undo. Şema ileri uyumlu olduğu için
      migration geri ALINMAZ.
   R2 Migration'ın kendisi hatalıysa: `alembic downgrade <önceki>` yalnızca downgrade'i test edilmiş,
      veri kaybetmeyen revizyonlar için (ör. 0004 sütun düşürür → kilitlenme sayaçları kaybolur, kabul edilebilir).
   R3 Veri bozulması: yazmayı durdur (bakım modu) → PITR / restore_verify ile doğrulanmış dump'tan geri yükle →
      son import'ları yeniden oynat → KPI önbelleği data_version ile kendiliğinden geçersizdir.
```
- KPI formül versiyonları DB düzeyinde değişmezdir (migration `0003`); geri almada formül geçmişi korunur.
- Redis'i geri almada boşaltmaya gerek yok: anahtarlar `CACHE_SCHEMA` + `data_version` içerir. Sonuç şeması değişen bir sürümde `CACHE_SCHEMA` artırılmalıdır.

---

## 4. Log yönetimi
- Uygulama stdout'a **tek satır JSON** yazar (`app/core/logging.py`): `ts, level, logger, msg, request_id, user_id, tenant_id, path, status, duration_ms` (+ `exc`). Kişisel veri (ad, e-posta, kimlik no, istek gövdesi) loglanmaz; yalnızca id'ler. `X-Request-ID` gelen istekten alınır veya üretilir ve yanıtta döner → log ile kullanıcı şikâyetini eşleştirmek için kullanın.
- Toplama: konteyner stdout → Fluent Bit / Vector → Loki veya OpenSearch. `request_id`, `tenant_id`, `status` alanlarını indeksleyin. Uvicorn access log'u JSON log ile mükerrer; üretimde `--no-access-log` önerilir (kendi `request` satırımız süre ve kullanıcı id'si içeriyor).
- Saklama: uygulama logları 30 gün sıcak + 1 yıl soğuk (şifreli). Denetim izi (`audit_logs` tablosu) **logdan ayrıdır**, veritabanında tutulur ve yedeklenir; saklama süresi KVKK politikası (SECURITY_PRIVACY.md) ile belirlenir.
- `path` alanı ham yoldur (id içerebilir, ör. `/api/v1/people/123/history`) — kişisel veri değil ama kişi id'si; log erişimi yetkili operasyon ekibiyle sınırlandırılmalı. Metriklerde ise yalnızca rota şablonu kullanılır.
- Hata logları: `unhandled error` + traceback, `request_id` ile; 5xx oranı alarmı (bölüm 2) bu logları işaret eder. Yük altında görülen tipik hata: `QueuePool limit ... connection timed out` → bkz. PERFORMANCE_REPORT (havuz/thread uyumu, PgBouncer).

## 5. Kapasite ve yapılandırma notları
- `HRSWI_DB_POOL_SIZE`/`HRSWI_DB_MAX_OVERFLOW` × worker sayısı < `max_connections`; üretimde PgBouncer (transaction pooling).
- `HRSWI_REDIS_URL` zorunlu kabul edin (çok worker'da süreç içi LRU paylaşılmaz). docker-compose `api` servisi `redis://redis:6379/0` kullanır.
- `HRSWI_METRICS_TOKEN` üretimde ayarlı olmalı.
- 15K veri setinde ölçülen kapasite ve darboğazlar: `docs/PERFORMANCE_REPORT.md` § 15K.
