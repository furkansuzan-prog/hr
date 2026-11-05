# WP-1 — İş kazası / meslek hastalığı istirahati için ayrı izin türü (`work_injury`)

> **Statü: Taslak — İK Direktörü incelemesi bekliyor.** Hazırlayan: Agent 18 (İSG Uzmanı). Onaylı değildir.
> Bu belge kod, katalog veya migration değiştirmez; mühendislik için spesifikasyondur. Örneklerin tamamı kurgusaldır.
> Sentetik demo veritabanı yalnızca kodun bugünkü davranışını doğrulamak için okunmuştur; hiçbir demo değeri şirket tabanı değildir.
> Kişi veya tekil olay değerlendirmesi yoktur; tüm göstergeler grup düzeyindedir.
> Dosya adı notu: `HR_TEAM_PLAN.md` §3 WP-1 çıktıyı `HSE-01_work_injury_leave_type.md` olarak adlandırır; bu belge görevlendirme talimatıyla `WP-1_work_injury_leave.md` adıyla yazıldı (içerik aynı iş paketidir).

| | |
|---|---|
| Tarih | 2026-11-05 |
| Sahip / inceleyen | Agent 18 → Agent 17 (İK Direktörü) → Orchestrator (ADR) |
| Girdiler | `TASK_BOARD.md` B-29, B-27; `docs/HR_DIRECTOR_RECOMMENDATIONS.md` §3.2, §3.3, §4, Ek E.2; `docs/DECISIONS.md` D-3, D-5, D-6, D-7; `backend/app/services/timekeeping.py` (`SGK_REPORT_CODES`, `_LEAVE_RULES`, `normalize_leave_type`); `backend/app/services/imports.py` (`LEAVE_FIELDS`, `_validate_leave`, `_commit_leave_row`); `backend/app/analytics/calculators.py` (`absenteeism_rate`, `protected_leave_share`, `_annual_leave_population`, `_safety_rate`); `backend/app/analytics/catalog.py` (KPI-003/028/053/054/055/056, KPI-014A/B, KPI-044 taslak); `backend/app/models/operations.py` (`LeaveRecord`, `SafetyEvent`); `backend/app/security/catalog.py` |
| Uygulayacaklar (öneri) | A05 (izin türü değer kümesi + veri migration'ı), A08 (`normalize_leave_type`, izin import'u yeniden sınıflandırma), A09 (KPI-003/028/053/055/056 formül versiyonları, uyum sinyali), A11 (erişim ve maskeleme), A12 (testler) |

---

## 1. Kapsam ve ilgili backlog ID'leri

- **B-29 (P1):** İş kazası ve meslek hastalığı istirahatleri için ayrı izin türü `work_injury`; KPI-053 dışında.
- **B-27 (Done):** izin import'unun `normalize_leave_type` kullanması — bu belge o eşlemeyi genişletir.
- **HR Director Ek E.2** (KPI-053 eki), **§3.2** (KPI-028 riski), **§3.3** (bildirimi caydırmama kuralları), **§5 #1**.
- **D-3** (KPI-003 şirket tanımı — raporlu günü içerir), **D-5** (KPI-053), **D-6** (korunan izin her zaman `parental`, önce kontrol edilir), **D-7** (KPI-055/056).
- Kapsam dışı: İSG olay import'unun alan sözlüğü (→ `WP-6_safety_import_fields.md`), oran korkulukları ve ramak kala (→ `WP-2_safety_rate_safeguards.md`).

## 2. Mevcut davranış (kod/doküman referansıyla)

### 2.1 Eşleme

`timekeeping.py`:

```python
SGK_REPORT_CODES = {"1": "sick", "2": "sick", "3": "sick", "4": "parental"}  # iş kazası, meslek hast., hastalık, analık
```

`_LEAVE_RULES` sırası: `parental` → `public_holiday` → `unexcused` → `sick` → `annual` → `unpaid`; `sick` iğneleri arasında `"iş kazası"`, `"is kazasi"`, `"istirahat"`, `"rapor"` vardır. Bilinmeyen etiket → `other`.

Bugünkü fonksiyonun gerçek çıktısı (salt okunur çalıştırıldı, 2026-11-05):

| Kaynak etiket / kod | Bugünkü sonuç | Sorun |
|---|---|---|
| SGK kodu `1` (iş kazası) | `sick` | KPI-028/053'e girer → kaza gizleme baskısı |
| SGK kodu `2` (meslek hastalığı) | `sick` | Aynı |
| `"İş kazası istirahati"` | `sick` | Aynı |
| `"İŞ KAZASI"` (büyük harf) | `other` | Python `lower()` `İ`'yi `i̇` (i + birleşik nokta) yapar, iğne eşleşmez → KPI-053'e değil KPI-003'e girer; etiket "eşlenemedi" uyarısı alır. Aynı kayıt yazımına göre iki farklı türe düşüyor |
| `"Meslek hastalığı"` | `other` | Meslek hastalığı iğnesi yok |
| `"MESLEK HASTALIĞI RAPORU"` | `sick` | `"rapor"` iğnesi yakalıyor — sonuç yazıma bağlı |
| `"work injury"`, `"Производственная травма"` | `other` | EN/RU iş kazası iğnesi yok |
| SGK kodu `5` (tanımsız) | etikete düşer → `other` | Bilinmeyen kod için ayrı uyarı yok |

### 2.2 KPI'lar (tür listesine göre filtre)

| KPI | Parametre (`absence_types`) | `work_injury` türü açılınca **kod değişmeden** ne olur |
|---|---|---|
| KPI-003 (D-3) | `["unexcused","sick","annual","unpaid","other"]` | **Sessizce hariç kalır** (liste dışı). D-3 raporlu günü içerdiği için bu, ürün sahibi kararının fiilen değişmesidir → açık karar gerekir (§3.3) |
| KPI-028 | `["sick"]` | Hariç kalır |
| KPI-053 (D-5) | `["unexcused","sick"]` | Hariç kalır (B-29'un amacı) |
| KPI-054 (D-6) | `["parental"]` | Etkilenmez |
| KPI-055/056 (D-7) | `_annual_leave_population`: uzun yokluk türleri sabit `('annual','sick','parental','unpaid')` | **`work_injury` uzun yokluk sayılmaz** → aylarca iş kazası istirahatinde olan kişi KPI-056 paydasında "izin kullanmayan" görünür; yaralanma KPI-056'yı kötüleştirir (ters teşvik) |
| KPI-014A/B | `safety_events` tablosundan; izin kayıtlarını okumaz | Doğrudan etkilenmez; uyum sinyali önerisi §3.5 |

KPI-003'ün `breakdown.excluded_hours_by_type` / `included_hours_by_type` alanları tür bazında saat döndürür; KPI-003 yalnızca kişi sayısı (`persons` ≥ `HRSWI_MIN_GROUP_SIZE`) eşiğiyle maskelenir. `work_injury` saati küçük bir grupta tek bir kişinin istirahatini ele verebilir (KVKK md. 6) → §3.6.

### 2.3 Yeniden sınıflandırma (import)

`_validate_leave` / `_commit_leave_row` "aynı kayıt" kontrolünü `(employment_id, leave_type, start_date, end_date)` ile yapar. Aynı istirahat raporu sonradan **farklı türle** gelirse (ör. SGK incelemesi sonucu iş kazası → hastalık) eşleşme bulunmaz; yalnızca `overlapping_leave` **uyarısı** üretilir ve commit **ikinci bir kayıt ekler** → aynı kişi-günler iki kez sayılır. `LeaveRecord` modelinde `source_record_id`, `record_version` alanları (SourceMixin) vardır ancak izin import'u bunları eşleştirme anahtarı olarak kullanmaz.

### 2.4 Veri tabanı ve erişim

- `leave_records.leave_type` `String(20)`, DB düzeyinde CHECK/enum yok (`0001_initial_schema.py`); yeni değer şema değişikliği gerektirmez, ama **ortak sözleşme**dir (model yorumu, import, KPI parametreleri, API/rapor etiketleri).
- `timekeeping.read` izni (HR, proje müdürü, departman müdürü, alt yüklenici müdürü, operasyon direktörü) "devamsızlık verisi"ni kapsar; bugün kişi düzeyi izin listesi sunan bir API yok (yalnızca KPI agregaları ve import veri kalite kuyruğu).
- Import ham satırları `import_staging_rows.raw` içinde tutulur → geçmişte SGK kodu taşıyan izin satırları geriye dönük yeniden eşlenebilir (§3.7).

## 3. Önerilen kural

### 3.1 Tanım

**`work_injury` — İş kazası veya meslek hastalığı nedeniyle istirahat (geçici iş göremezlik):** çalışanın, 5510 md. 13 anlamında iş kazası veya md. 14 anlamında meslek hastalığı nedeniyle hekim raporu / SGK geçici iş göremezlik kaydı ile işten uzak kaldığı süre. ⚖️ (madde içerikleri teyit edilmeli)

- İşverenin veya yöneticinin **beyanı değil, rapor türü** belirleyicidir: SGK rapor/vaka türü kodu varsa o esas alınır.
- `work_injury` bir **izin türü**dür, kaza sınıfı değildir. Kazanın sınıfı (LTI, kısıtlı iş, tıbbi müdahale…) İSG kaydında (`safety_events`) tutulur; iki kayıt kişi düzeyinde **birbirine bağlanmaz** (`SafetyEvent` kişi kimliği taşımaz — bilinçli tasarım). Uyum yalnızca grup düzeyinde sayılarla kontrol edilir (§3.5).
- Önerim tek tür: iş kazası ve meslek hastalığı aynı `work_injury` değerine eşlenir (veri minimizasyonu; HR_TEAM_PLAN WP-1 kabul kriteri). Alt tür ayrımı açık soru Q1.

### 3.2 Eşleme kuralı (A08 — `normalize_leave_type`)

**Öncelik sırası (değişmez ilke: korunan izin önce, D-6):**

1. SGK rapor türü kodu dolu ve tanınıyorsa → kod tablosu (etiket yok sayılır, çelişki uyarısı üretilir).
2. Etiket kuralları sırası: `parental` → `public_holiday` → `unexcused` → **`work_injury`** → `sick` → `annual` → `unpaid` → `other`.
3. Etiket karşılaştırması **Türkçe güvenli katlama** ile yapılır (`İ→i`, `I→i`, `ı→i`, aksan sökme — `imports._norm` ile aynı mantık); `lower()` tek başına yeterli değildir.

**SGK rapor türü → izin türü:**

| SGK kodu | SGK anlamı | Bugün | Önerilen | Not |
|---|---|---|---|---|
| 1 | İş kazası | `sick` | **`work_injury`** | ⚖️ 5510 md. 13 |
| 2 | Meslek hastalığı | `sick` | **`work_injury`** | ⚖️ 5510 md. 14 |
| 3 | Hastalık | `sick` | `sick` | değişmez |
| 4 | Analık | `parental` | `parental` | D-6, değişmez |
| diğer / boş olmayan tanınmayan kod | — | etikete düşer | etikete düşer **+ `unmapped_sgk_report_code` uyarısı** (veri kalite kuyruğu) | ⚖️ SGK vaka/rapor türü kod listesinin güncel hali gerçek dosyayla teyit edilmeli |

**Etiket iğneleri (`work_injury`)** — katlanmış metinde alt dize olarak; tek başına "kaza" / "accident" kullanılmaz (özel trafik kazası, ev kazası iş kazası değildir):

| Dil | İğneler (katlanmış) | Eşlenecek örnek etiketler |
|---|---|---|
| TR | `is kaza`, `meslek hastal` | "İş kazası", "İŞ KAZASI", "İş kazası istirahati", "İş Kazası Raporu", "Meslek hastalığı", "MESLEK HASTALIĞI RAPORU" |
| EN | `work injury`, `work-related injury`, `work related injury`, `occupational injury`, `occupational accident`, `work accident`, `industrial accident`, `industrial injury`, `occupational disease`, `occupational illness`, `injury on duty` | "Work injury leave", "Occupational disease", "Industrial accident sick leave" |
| RU | `производствен` (травма), `несчастн` (случай на производстве), `профзаболев`, `профессиональное заболев` | "Производственная травма", "Несчастный случай на производстве", "Профзаболевание" |

**Eşleme sonucu tablosu (kabul testinin girdisi):**

| Girdi (etiket, kod) | Beklenen |
|---|---|
| ("Rapor", "1") | `work_injury` |
| ("Hastalık raporu", "1") | `work_injury` + uyarı `leave_label_code_conflict` |
| ("İş kazası istirahati", "3") | `sick` + uyarı `leave_label_code_conflict` (resmî kod esas; çelişki İSG incelemesine görünür) |
| ("Analık raporu", "4") | `parental` |
| ("İş kazası", boş) / ("İŞ KAZASI", boş) | `work_injury` |
| ("Meslek hastalığı", boş) | `work_injury` |
| ("work injury", boş), ("Производственная травма", boş) | `work_injury` |
| ("Raporlu", boş), ("istirahat", boş) | `sick` |
| ("Trafik kazası raporu", boş) | `sick` (iş kazası olup olmadığını etiket söylemez; kod gelirse kod esas) |
| Etikette hem `parental` hem `work_injury` iğnesi varsa (ör. "Analık – iş kazası sonrası") | `parental` + uyarı `leave_label_ambiguous` (D-6 önceliği korunur; İK incelemesi) |
| ("XYZ", "7") | `other` + `unmapped_sgk_report_code` + `unmapped_leave_label` |

### 3.3 KPI etkisi

| KPI | Öneri | Formül versiyonu |
|---|---|---|
| **KPI-053** | `work_injury` **hariç** (parametre aynı kalır: `["unexcused","sick"]`). `excl` metnine "iş kazası/meslek hastalığı istirahati (`work_injury`)" eklenir | Yeni versiyon (dışlama metni + veri anlamı değişir; eski sonuçlar v-önceki ile korunur) |
| **KPI-028** | `work_injury` **hariç**; ad "Hastalık Devamsızlık Oranı (iş kazası hariç)" olarak netleşir | Yeni versiyon |
| **KPI-003** | Karar Agent 17'de (D-3'e dokunduğu için ürün sahibine de gider). Seçenekler aşağıda | Hangi seçenek olursa olsun **parametre açıkça yazılır**; "liste dışında kaldı" ile sessiz değişiklik yok |
| **KPI-055/056** | Uzun yokluk türlerine `work_injury` eklenir: `('annual','sick','parental','unpaid','work_injury')` içinde `<> 'annual'` toplamı | Yeni versiyon (Agent 19 WP-4 ile birlikte; çift iş yapılmasın) |
| **KPI-054** | Değişmez | — |
| **KPI-014A/B** | Değişmez (olay tabanlı). Uyum sinyali §3.5 | — |

**KPI-003 seçenekleri:**

| Seçenek | Tanım | Sonuç |
|---|---|---|
| A — Dahil | `absence_types`'a `work_injury` eklenir | D-3 metnine sadık ("raporlu gün" dahil). Kaza istirahati, eşikli (7/9/11) bir göstergede kalır → gizleme baskısı KPI-003 üzerinden sürer. KPI-003 primsiz olsa da (§3.2 öneri) birim karşılaştırmasında kaza yaşayan birim kötü görünür |
| B — Hariç | Liste aynen kalır, dışlama açıkça yazılır | Baskı kalkar; "işte olmama" oranı kaza istirahati kadar eksik gösterir; planlama kaybı başka yerde görünmez |
| **C — Hariç + İSG bağlamında ayrı, eşiksiz izleme (öneri)** | B + `work_injury` saatleri yalnızca İSG panelinde eşiksiz bir planlama/uyum figürü olarak (KPI-054 deseni: `neutral`, eşik yok, iki koşullu bastırma) ve kayıp gün KPI-044 (taslak) üzerinden izlenir | Hiçbir eşikli devamsızlık göstergesinde kaza istirahati kalmaz; kapasite kaybı görünür kalır. İK Direktörü ön görüşüyle uyumlu (HR_TEAM_PLAN WP-1). **D-3 değişikliği → Orchestrator `DECISIONS.md`'ye yeni karar satırı** |

Seçenek C'deki eşiksiz figür: **"İş Kazası/Meslek Hastalığı İstirahat Payı"** = `work_injury` saatleri (döneme oranlanmış) / onaylı puantajdaki planlı saat × 100; `neutral`, eşik yok, prim/hedef yok; **bastırma:** dönemde `work_injury` kaydı olan farklı kişi sayısı 1–4 ise **veya** grup < `HRSWI_MIN_GROUP_SIZE` ise gösterilmez; proje altı kırılım yok; görünürlük `hse.read` + `timekeeping.read` birlikte olan roller (öneri, A11 ve Agent 20 teyidi). Yeni bir KPI kodu mu yoksa KPI-044 sayfasında yardımcı figür mü olacağı Orchestrator kararıdır.

### 3.4 Yeniden sınıflandırma kuralı (A08)

1. **Kimlik anahtarı:** izin kaydı, kaynakta `source_record_id` (rapor/izin no) varsa `(tenant, source_system, source_record_id)` ile; yoksa `(employment_id, start_date, end_date)` ve tür ailesi **"raporlu istirahat" = {`sick`, `work_injury`}** ile tanınır.
2. Aynı anahtarla farklı türde satır gelirse bu **yeni kayıt değil, yeniden sınıflandırmadır:** mevcut kayıt yerinde güncellenir (`leave_type` değişir, `record_version + 1`, denetim izi: eski tür, yeni tür, import batch, kullanıcı). İkinci satır **eklenmez**.
3. `parental` ↔ diğer türler arası geçiş aynı kuralla güncellenir ama D-6 gereği ayrıca `leave_reclassified_protected` uyarısı üretir (İK incelemesi).
4. Etkilenen en erken tarihten itibaren KPI'lar bayatlatılır (`mark_stale_from`, bugünkü import davranışı).
5. **Kapanmış dönem (D-2):** dondurulmuş sonuç değişmez. Dönem görünümünde "kapanıştan sonra N kayıt yeniden sınıflandırıldı" notu (yalnızca sayı) gösterilir; yeniden açma kararı ana yüklenicinin `period.close` yetkilisindedir.
6. **Yön sayacı (grup düzeyi):** çeyrek bazında `work_injury → sick` ("aşağı") ve `sick → work_injury` ("yukarı") sayıları kapsam (firma/proje) düzeyinde tutulur; kişi listesi yok, kapsamda toplam < 5 ise yalnızca firma/tenant toplamında görünür. "Aşağı" yöndeki artış WP-2 bildirim kalitesi sinyallerine girer.
7. Tarihleri de değişen satır (kısmi örtüşme) otomatik eşleştirilmez → bugünkü `overlapping_leave` davranışı korunur, ancak tür ailesi {`sick`,`work_injury`} içinde örtüşme **hata** (manuel inceleme) olur; uyarı olarak geçip çift sayım üretmesine izin verilmez.

### 3.5 Uyum sinyali: izin ↔ İSG olay kaydı (A09, grup düzeyi)

Kişi bağlantısı kurulmadan, kapsam × çeyrek düzeyinde:

- `R` = dönemde **başlayan** `work_injury` istirahat dönemlerinin farklı kişi sayısı (kendi çalışanları; izin kaydı yalnızca onlarda var).
- `K` = aynı kapsamda, dönem + önceki 30 gün içinde olay tarihi olan **kendi çalışanı** vakaları: `lost_time_injury` + `fatality` (çalışan kategorisi alanı WP-6'da önerilir).
- **Sinyal "istirahat var, kayıp günlü vaka yok":** `R > K` → olası İSG kaydı eksikliği veya meslek hastalığı istirahati (tek tür seçilirse gürültü kaynağı, Q1).
- **Sinyal "kayıp günlü vaka var, iş kazası istirahati yok":** `K > R` → istirahatin yıllık izin / hastalık / "idari izin" olarak yazılması şüphesi veya izin beslemesi eksik (B-20).
- Sinyal **renk değil**, İSG uzmanı + İK Direktörü için inceleme tetikleyicisidir; sayılar `R`, `K` < 5 olan kapsamlarda yalnızca firma/tenant düzeyine yuvarlanır. Hiçbir kişi, olay no veya tarih listesi üretmez.

### 3.6 KVKK md. 6 erişim ve maskeleme (A11 — Agent 20 risk notu bekleniyor)

- `work_injury` kaydı sağlık verisi niteliğindedir (iş kazası / meslek hastalığı nedeniyle rapor). ⚖️
- KPI-003 (Seçenek A seçilirse) ve KPI-053/028 `breakdown` alanlarında `work_injury` saati, **`work_injury` kaydı olan farklı kişi < `HRSWI_MIN_GROUP_SIZE`** ise maskelenir (`null`); aynı kural `parental` için de önerilir (KPI-054 ile tutarlı).
- Veri kalite kuyruğu (`data_quality_issues.message`) eşlenen sağlık etiketini kişi referansıyla birlikte **yankılamaz**; mesaj kural kodunu ve "sağlık kaynaklı izin etiketi" ifadesini taşır. Kuyruk zaten `import.write` rollerine açıktır.
- Kişi düzeyi izin listesi ileride açılırsa (`timekeeping.read`), `work_injury` türü yalnızca İK operasyon rolünde tür adıyla, diğer rollerde "raporlu" olarak görünür (öneri; A11 kararı).
- Tanı, ICD kodu, yaralanma türü/vücut bölgesi **hiçbir zaman** alınmaz (`_HEALTH_COLUMN_HINTS` mevcut davranış korunur ve WP-6 listesiyle genişletilir).

### 3.7 Geçmiş veri (A05 veri migration'ı)

- Daha önce import edilmiş ve `import_staging_rows.raw` içinde SGK kodu 1/2 veya §3.2 iğneleriyle eşleşen etiket taşıyan `sick`/`other` kayıtları `work_injury`'ye taşınabilir. Bu bir **veri migration'ıdır** (yeni Alembic revizyonu, eski revizyon değişmez), denetim izli ve geri alınabilir olmalı; kapanmış dönemler §3.4/5 ile korunur.
- Elle girilmiş / ham satırı olmayan kayıtlar taşınamaz → "geçmiş dönemde iş kazası istirahati `sick` içinde olabilir" notu formül versiyonu açıklamasına yazılır.

## 4. El hesaplı örnekler (tablo)

Tüm örnekler kurgusaldır. Eşikler: KPI-053 2,5 / 3,5 / 5 · KPI-028 2,0 / 3,0 / 4,5 · KPI-003 7 / 9 / 11 (lower_better; uyarı değerine eşit = sarı).

### Örnek 1 — Plan örneği (HR_TEAM_PLAN WP-1)

Kapsam: bir proje, bir ay. Planlı saat 10.000. Habersiz 50 sa, hastalık 150 sa, iş kazası istirahati 80 sa; ayrıca yıllık izin 400, ücretsiz 30, diğer 20 sa.

| KPI | Bugün (iş kazası `sick` içinde) | Kural sonrası | Renk bugün → sonra |
|---|---|---|---|
| KPI-053 | (50 + 150 + 80) / 10.000 = **%2,80** | (50 + 150) / 10.000 = **%2,00** | yeşil → yeşil |
| KPI-028 | (150 + 80) / 10.000 = **%2,30** | 150 / 10.000 = **%1,50** | yeşil → yeşil |
| KPI-003 Seçenek A | 50+150+80+400+30+20 = 730 → **%7,30** | 730 → **%7,30** | yeşil → yeşil |
| KPI-003 Seçenek B/C | 730 → **%7,30** | 650 / 10.000 = **%6,50** | yeşil → yeşil |
| İstirahat payı (Seçenek C) | — | 80 / 10.000 = **%0,80** (eşiksiz; `work_injury` kaydı olan kişi ≥ 5 değilse gösterilmez) | — |

### Örnek 1b — Rengin kaza istirahatiyle değiştiği durum (baskının kaynağı)

Planlı 10.000 sa; habersiz 100, hastalık 230, iş kazası istirahati 120 sa.

| KPI | Bugün | Kural sonrası | Renk |
|---|---|---|---|
| KPI-053 | 450 / 10.000 = **%4,50** | 330 / 10.000 = **%3,30** | **sarı → yeşil** |
| KPI-028 | 350 / 10.000 = **%3,50** | 230 / 10.000 = **%2,30** | **sarı → yeşil** |

Yorum: bugün tek bir iş kazası istirahati birimi sarıya taşıyabilir; birim yöneticisi için en kolay "düzeltme" istirahatin rapora dönüşmemesi, yıllık izne yazılması veya kazalının erken işe çağrılmasıdır. Kural bu bağı koparır.

### Örnek 2 — Sınır durumu: yeniden sınıflandırma (iş kazası → hastalık)

Kişi R'nin istirahati: 02.03.2026 (Pzt) – 11.03.2026 (Çar), 10 takvim günü, 8 iş günü × 9 = 72 sa, kaynak rapor no `RPR-7781`. Mart: planlı 10.000 sa; R dışındaki çalışanlarda habersiz 50, hastalık 150 sa.

| Adım | Bugünkü import davranışı | Önerilen davranış |
|---|---|---|
| Nisan başı: Mart import'u, SGK kodu 1 | `work_injury` yok → `sick` 72 sa | `work_injury` 72 sa |
| Mart KPI-053 | (50 + 150 + 72) / 10.000 = %2,72 | (50 + 150) / 10.000 = **%2,00** |
| Mayıs: SGK incelemesi sonucu aynı rapor kod 3 ile yeniden geliyor | (Bugün zaten `sick`; tür aynı → "already_imported", sorun yok) | Anahtar `RPR-7781` eşleşir → **yerinde güncelleme** `work_injury → sick`, `record_version` 2, "aşağı" sayacı +1 |
| Mart KPI-053 (yeniden hesap) | %2,72 | (50 + 150 + 72) / 10.000 = **%2,72** |
| B-29 uygulanmış ama §3.4 uygulanmamış olsaydı | — | Yeni satır eklenirdi: `sick` 72 + `work_injury` 72 → R için 8 iş gününde **144 sa** kayıt (çift sayım); KPI-053 %2,72 görünür ama istirahat payı %0,72 yanlış kalır, KPI-003 Seçenek A'da 72 sa iki kez sayılır |
| Mart, D-2 ile kapatılmışsa | — | Dondurulmuş KPI-053 **%2,00 kalır**; dönem görünümünde "kapanıştan sonra 1 kayıt yeniden sınıflandırıldı" notu; canlı yeniden hesap ancak dönem açılırsa |

Ters yön (hastalık → iş kazası; ör. SGK/mahkeme kararıyla iş kazası kabulü): aynı adımlar; KPI-053 %2,72 → **%2,00**, "yukarı" sayacı +1. Ters yönde sayaç artışı olumsuz değildir.

### Örnek 3 — KPI-056 ters teşviki (sınır durumu)

Ekip: 1 yılını doldurmuş 20 uygun çalışan, 12 aylık pencere. 1 kişi 7 ay `work_injury` istirahatinde, yıllık izin kullanmadı; diğer 19 kişiden 1'i izin kullanmadı.

| | Bugün (uzun yokluk türlerinde `work_injury` yok) | Kural sonrası |
|---|---|---|
| KPI-056 payda | 20 | 19 (istirahatteki kişi "uzun yokluk" olarak hariç, sayı lineage'da) |
| KPI-056 pay | 2 | 1 |
| KPI-056 | 2 / 20 = **%10,0** (uyarı eşiğine eşit → **sarı**) | 1 / 19 = **%5,26** (yeşil; 5 ≤ x < 10) |

(Grup 20 kişi ≥ 5 olduğu için görünür; kişi bilgisi yok.)

### Örnek 4 — Uyum sinyali (§3.5)

Proje, 3. çeyrek: dönemde başlayan `work_injury` istirahati olan farklı kişi `R = 6`; dönem + önceki 30 günde kendi çalışanı LTI + ölümlü vaka `K = 2`.
→ `R > K` (6 > 2): "istirahat var, kayıp günlü vaka yok" sinyali; değerler 5'ten büyük olduğu için proje düzeyinde gösterilir. `R = 3, K = 1` olsaydı aynı sinyal yalnızca firma toplamında görünürdü.

## 5. Kabul kriterleri

1. SGK kodu 1 ve 2 → `work_injury`; 3 → `sick`; 4 → `parental`; tanınmayan dolu kod → etiket kuralına düşer ve `unmapped_sgk_report_code` uyarısı üretir. (§3.2 tablosundaki tüm satırlar birebir geçer.)
2. Etiket eşlemesi Türkçe güvenli katlama ile yapılır: "İŞ KAZASI", "İş kazası", "is kazasi", "MESLEK HASTALIĞI RAPORU", "Meslek hastalığı" aynı sonucu (`work_injury`) verir; EN ve RU örnekleri geçer; "Analık raporu" hâlâ `parental` (D-6 önceliği).
3. Kod–etiket çelişkisinde kod kazanır ve `leave_label_code_conflict` uyarısı veri kalite kuyruğuna yazılır.
4. Örnek 1: KPI-053 = **2,00**, KPI-028 = **1,50**; KPI-003 seçilen seçeneğe göre **7,30** (A) veya **6,50** (B/C). Örnek 1b: KPI-053 **3,30** yeşil, KPI-028 **2,30** yeşil.
5. KPI-003 formül versiyonunun `absence_types` parametresi seçilen seçeneği açıkça içerir/dışlar ve `excl`/`incl` metni `work_injury`'yi adıyla anar; `DECISIONS.md`'de D-3'ü güncelleyen karar satırı vardır.
6. Örnek 2: yeniden sınıflandırma ikinci kayıt **oluşturmaz**; kişi-gün başına toplam izin saati değişmez (72); `record_version` artar; denetim kaydı eski/yeni türü taşır; kapanmış dönem değeri değişmez ve yeniden sınıflandırma notu sayıyla görünür.
7. {`sick`, `work_injury`} içinde tarih örtüşmesi **hata** olarak işaretlenir (commit edilmez).
8. Örnek 3: KPI-056 = **5,26** (`work_injury` uzun yokluk sayılır).
9. `work_injury` saatleri, kaydı olan kişi sayısı < `HRSWI_MIN_GROUP_SIZE` olan kapsamlarda hiçbir KPI breakdown'unda görünmez.
10. Uyum sinyali (Örnek 4) yalnızca sayı döndürür; hiçbir yanıt kişi kimliği, olay no veya tarih listesi içermez.
11. Mevcut testler (`test_leave_type_mapping_protects_parental`, `test_unplanned_absence_and_company_absenteeism_exclude_parental`, `test_formula_change_preserves_old_results`) değişmeden geçer.
12. Geçmiş veri migration'ı yalnızca ham satırı SGK kodu/etiket kuralıyla eşleşen kayıtları taşır, taşınan sayıyı raporlar ve geri alınabilir.

## 6. Önerilen test adları

- `test_sgk_codes_1_and_2_map_to_work_injury`
- `test_sgk_code_4_still_parental_precedence`
- `test_unknown_sgk_code_flags_quality_issue`
- `test_work_injury_labels_tr_en_ru_casefold` (büyük harfli Türkçe dahil)
- `test_work_injury_checked_before_sick`
- `test_leave_label_code_conflict_code_wins_with_warning`
- `test_kpi053_excludes_work_injury_exact` (2,80 → 2,00; 4,50 → 3,30)
- `test_kpi028_excludes_work_injury_exact` (2,30 → 1,50)
- `test_kpi003_work_injury_explicit_parameter` (seçilen seçenek)
- `test_kpi056_long_absence_includes_work_injury` (10,0 → 5,26)
- `test_leave_reclassification_updates_in_place_no_double_count`
- `test_leave_reclassification_closed_period_frozen_with_note`
- `test_sick_work_injury_overlap_is_error`
- `test_reclassification_direction_counts_group_level`
- `test_work_injury_breakdown_masked_below_min_group`
- `test_work_injury_lost_time_reconciliation_signal_counts_only`
- `test_quality_issue_message_does_not_echo_health_label`
- `test_backfill_work_injury_from_staging_raw`

## 7. Etkilenen sözleşmeler / ADR gerekli mi

**ADR gerekli — evet** (AGENTS.md çalışma protokolü 4: ortak sözleşme değişikliği).

| Sözleşme | Değişiklik | Sahip |
|---|---|---|
| İzin türü değer kümesi (`LeaveRecord.leave_type`, model yorumu, `normalize_leave_type` dönüş kümesi, rapor/ön yüz etiketleri TR/EN/RU) | `work_injury` eklenir | A05, A08, A07 |
| `SGK_REPORT_CODES` | 1, 2 → `work_injury` | A08 |
| İzin import'u kimlik/yeniden sınıflandırma kuralı | §3.4 | A08 |
| KPI-003/028/053/055/056 formül versiyonları | §3.3 | A09 (KPI-055/056 Agent 19 WP-4 ile birleştirilir) |
| `KpiResult.breakdown` maskeleme | §3.6 | A09, A11 |
| D-3 | KPI-003'ün `work_injury` kararı | Ürün sahibi → Orchestrator (`DECISIONS.md`) |
| Veri migration'ı (geçmiş kayıtlar) | §3.7 — yeni Alembic revizyonu | A05 |

## 8. Teşvik ve manipülasyon riski (bildirim karşıtı teşvik analizi)

| Risk | Bugün | Kural sonrası | Kontrol |
|---|---|---|---|
| Kaza istirahatini KPI-053/028'den kurtarmak için kazanın SGK'ya bildirilmemesi, istirahatin yıllık izne / "idari izne" yazılması, kazalının erken işe çağrılması (presenteeism) | **Yüksek** (Örnek 1b) | Düşük — istirahat eşikli göstergelerde yok | Uyum sinyali `K > R`; İSG oranları ve istirahat payı prime bağlanmaz |
| Baskının yeni göstergeye taşınması (istirahat payı eşikli olursa) | — | Orta | İstirahat payı **eşiksiz, `neutral`, hedefsiz** kalır; hiçbir birim sıralamasında kullanılmaz |
| Ters yönde oyun: hastalığı iş kazası gibi göstererek KPI-053'ü düşürmek | — | Düşük — tür yönetici beyanından değil SGK rapor türünden gelir; iş kazası kaydı LTIFR'ı (KPI-014A) artırır, yani "kazanç" yoktur | Kod–etiket çelişki uyarısı; "yukarı" sayacı |
| SGK sürecinde iş kazasının "hastalık"a çevrilmesi (rücu / sorumluluk kaygısıyla) | Görünmez | Görünür: "aşağı" yeniden sınıflandırma sayacı | Sayaç WP-2 bildirim kalitesi sinyallerine girer; ⚖️ 5510 md. 21 rücu riski hukukçuya |
| Yeniden sınıflandırmada çift kayıt (veri hatası) yüzünden değerlerin şişmesi, kaynağa güveni azaltması | Mümkün | Engellenir (§3.4) | Örtüşme hatası |
| KPI-056'nın yaralananı "izin kullanmayan" sayması | Var (Örnek 3) | Kalkar | Uzun yokluk türü |
| Kaza istirahatinin gizlenmesi için izin beslemesinin hiç gönderilmemesi | — | — | B-20 kapsam meta verisi: besleme yoksa `insufficient_data`, sıfır değil (Agent 19 DATA-03) |

Genel kural: `work_injury` ile ilgili hiçbir sayı kişi, ustabaşı, şantiye şefi veya alt yüklenici primine / değerlendirmesine bağlanmaz (HR Director §3.3 kural 1).

## 9. ⚖️ maddeler

- ⚖️ **5510 md. 13** (iş kazası tanımı; işverenin SGK'ya bildirim yükümlülüğü — 3 iş günü) ve **md. 14** (meslek hastalığı tanımı ve bildirimi) — güncel metin **teyit edilmeli**.
- ⚖️ **6331 md. 14** (iş kazası ve meslek hastalıklarının kayıt ve bildirimi) — **teyit edilmeli**.
- ⚖️ **5510 md. 18** (geçici iş göremezlik ödeneğinin iş kazası/meslek hastalığında ve hastalıkta hangi günden başladığı) — sınıflandırmanın mali sonuçları olduğu için **teyit edilmeli**; bu belge mali sonuca dayanmaz.
- ⚖️ **5510 md. 21** (iş kazasında işverene rücu) — "aşağı" yeniden sınıflandırma baskısının kaynağı olabilir; **hukukçuya soru**.
- ⚖️ **KVKK (6698) md. 6** — iş kazası/meslek hastalığı istirahat türü sağlık verisi; işleme şartı, rol kısıtı ve minimum grup — **Agent 20 (LEGAL-01) ve KVKK irtibat kişisi teyidi**.
- ⚖️ SGK rapor/vaka türü kod listesinin (1–4 ve varsa diğerleri) güncel hali — kaynak dosyayla **teyit edilmeli**.
- ⚖️ Kapanmış dönemde yeniden sınıflandırmanın raporlama (GRI/ESRS) açısından yeniden düzenleme (restatement) gerektirip gerektirmediği — **teyit edilmeli**.

## 10. Açık sorular (Agent 17 için)

- **Q1 — Alt tür:** iş kazası ve meslek hastalığı tek `work_injury` mi (öneri: evet; veri minimizasyonu), yoksa `work_injury` + `occupational_disease` mi? Tek tür, §3.5 uyum sinyalini meslek hastalığı istirahatleriyle gürültülü yapar.
- **Q2 — KPI-003:** Seçenek A / B / **C (öneri)**? C seçilirse D-3 için ürün sahibi onayı gerekir.
- **Q3 — İstirahat payı:** ayrı KPI kodu mu, KPI-044 (Kayıp Gün Oranı) sayfasında yardımcı figür mü?
- **Q4 — Rol görünürlüğü:** istirahat payını kimler görür? Öneri: `hse.read` ve `timekeeping.read` birlikte olan roller; proje müdürü yalnızca kendi projesinde ve bastırma kurallarıyla.
- **Q5 — Geçmiş veri migration'ı** yapılsın mı (öneri: evet, ham satırı olanlar için), yoksa yalnızca ileriye dönük mü?
- **Q6 — Uyum sinyali penceresi:** 30 günlük geriye bakış yeterli mi (kaza ile istirahat başlangıcı arası)? Gerçek veride kalibre edilmeli.

## 11. İnsan İSG profesyoneli / hukukçu gerektirenler

- SGK rapor türü kodlarının gerçek kaynak dosyada doğrulanması ve iş kazası / meslek hastalığı kararının **hiçbir zaman platformca verilmemesi** (platform yalnızca kaynağın türünü taşır) — işyeri hekimi / iş güvenliği uzmanı (6331 md. 8).
- Seçenek C'nin ve yeniden sınıflandırma sayaçlarının İSG Kurulunda (6331 md. 22) görüşülmesi; çalışan temsilcisinin görüşü.
- KVKK md. 6 işleme dayanağı ve rol listesi — KVKK irtibat kişisi.

## 12. Statü

**Taslak — İK Direktörü incelemesi bekliyor.** Onaylı değildir. "İK Direktörü incelemesi" bölümü Agent 17 tarafından eklenecektir.
