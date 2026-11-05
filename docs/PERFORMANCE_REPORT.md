# HRSWI — Performans Ölçüm Raporu (geliştirme ortamı) · Agent 13

> Bu değerler **geliştirme makinesinde** alınmıştır; üretim kapasitesi iddiası değildir. Hedefler (15K aktif personel, 150 eşzamanlı kullanıcı) **henüz doğrulanmadı**.

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
