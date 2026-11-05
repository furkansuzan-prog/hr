# İK Ekibi Sprint 1 — İK Direktörü İnceleme Özeti

> **Statü:** İK Direktörü (Agent 17) incelemesi, 2026-11-05. Kod, katalog veya `DECISIONS.md` değiştirmez.
> Ürün kararı gerektiren maddeler ürün sahibine, hukuki maddeler insan hukukçuya gider; değerleri platform sahibi
> onaydan sonra uygular. Platformda yalnızca **sentetik demo veri** vardır; hiçbir sayı şirket tabanı değildir.
> Ayrıntılı gerekçeler her teslimatın sonundaki "## İK Direktörü incelemesi (2026-11-05)" bölümündedir.

---

## 1. Teslimatlar ve kararlar

| WP | Teslimat | Sahip | Karar | Koşul sayısı | El hesabı kontrolü |
|---|---|---|---|---|---|
| WP-1 | `hse/WP-1_work_injury_leave.md` — `work_injury` izin türü (B-29) | Agent 18 | **Şartlı onay** | 7 | 6 örnek yeniden hesaplandı — hepsi eşleşiyor |
| WP-2 | `hse/WP-2_safety_rate_safeguards.md` — İSG oranı korkulukları, ölümlü kaza uyarısı, KPI-043, sinyaller | Agent 18 | **Şartlı onay** | 7 | 10 örnek + Poisson tablosunun tamamı — eşleşiyor |
| WP-6 (İSG) | `hse/WP-6_safety_import_fields.md` — İSG olay import'u | Agent 18 | **Şartlı onay** | 6 | Örnek dosya 4.1, oranlar 4.2, çok vakalı olay 4.3 — eşleşiyor |
| WP-4 | `data/WP-4_work_pattern_leave_conversion.md` (+ `WP-4_examples.csv`) — çalışma düzeni ve izin hakkı (B-28) | Agent 19 | **Şartlı onay** | 4 | E1, E3 (gün sayıları dahil), E4, E6 (tarihler/tatiller), M3a, E9/E10, E12 — eşleşiyor |
| WP-5 | `data/WP-5_turnover_definitions.md` (+ `WP-5_sgk_exit_code_map.csv`) — devir tanımları | Agent 19 | **Şartlı onay** | 7 | H1–H5 gün sayıları, W1–W5, V1/V3/V6, R1/R3 — eşleşiyor; CSV ↔ belge eşik çelişkisi bulundu |
| WP-6 (veri) | `data/WP-6_import_field_dictionaries.md` — puantaj/eğitim/maliyet import'u + kapsam meta verisi (B-07, B-20) | Agent 19 | **Şartlı onay** | 6 | K1, K2, K3, K7, K8 + üç örnek dosya — eşleşiyor |
| WP-3 | `legal/WP-3_legal_question_file_01.md` — hukuki soru dosyası no. 1 (64 soru) | Agent 20 | **Şartlı onay** — Paket 1 gönderilebilir | 6 | WT-RU-01 eşleşiyor; AL-02 değeri doğru, **renk etiketi yanlış** (%77,8 motorda `good`, "uyarı bandı" değil) |

**İade yok.** Yedi teslimatın hepsi §2.4 şablonuna uyuyor. Hepsinde gerekçe yazılı, bildirim karşıtı teşvik / manipülasyon riski ele alınmış, kişi düzeyinde karar ya da sıralama yok, ⚖️ işaretleri tam. Koşulların çoğu çapraz tutarlılıkla ilgili, belgelerin içeriğiyle değil.

**Orchestrator doğrulama notlarının etkisi:**
- (a) Türkçe güvenli katlama zaten uygulandı. WP-1 §2.1'deki "büyük harfli etiket → `other`" satırları artık geçerli değil. Mühendislik işi küçüldü: `work_injury` kuralı `sick`'ten **önce** eklenecek, iğneler bu kurala taşınacak. Katlama testine eklenen 5 geçici satır WP-1 ile bilinçli olarak `work_injury`'ye çevrilecek (WP-1 koşul 2).
- (b) Alt yüklenici görünürlüğü `project_id`'yi yok sayıyor ve paylaşım alt kademelere yayılıyor. Bu durum, alt yüklenici verisi içeren yeni import'ların (İSG vakaları, puantaj) **gerçek veriyle önünde bir engeldir**. Düzeltme uygulama sırasında ilk dalgadadır (§5). Yayılım ve geçmiş dönem kapsamı ürün kararıdır (§4 PO-2).

## 2. Aldığım kararlar (İK yetkisi içinde)

| Konu | Karar | Kaynak |
|---|---|---|
| İş kazası + meslek hastalığı | Tek tür: `work_injury` | WP-1 Q1 |
| KPI-003 ve `work_injury` | **Seçenek C** (dışla + eşiksiz izleme), D-3'ü değiştirdiği için → PO-1 | WP-1 Q2 |
| İstirahat payı göstergesi | Ayrı, eşiksiz KPI kodu (KPI-054 deseni) | WP-1 Q3 |
| Geçmiş veri taşıma / yeniden çevrim | Yalnızca ham satırı olan kayıtlar ve **yalnızca açık dönemler**; kapalı dönemler dondurulmuş kalır | WP-1 Q5, WP-4 S-5 |
| Küçük İSG kapsamları | **C1**: renk yok, değer ve olay sayısı gösterilir | WP-2 Q1 |
| TRIR minimum maruziyet | **500.000 sa** (önceki öneri başlangıcım 200.000'di). D-5 eşik değerleri değişmez | WP-2 Q2 |
| KPI-043 yönü | Kalıcı olarak `neutral`, eşik yok | WP-2 Q3 |
| Ölümlü kaza bandı | KPI-014A'yı görebilen herkes görür (sayı + proje + ay) | WP-2 Q7 |
| İSG sinyalleri S1–S5 | Başlangıç değerleri kabul, güven düşük, 12 ay sonra kalibre edilecek | WP-2 Q5 |
| İSG import'u | Vaka düzeyi; meslek hastalığı v1'de reddedilir ama kuyrukta sayılır; KPI-044'te 180 gün tavanı (tavansız toplam da saklanır) | WP-6 İSG Q1–Q6 |
| Düzen kapsamı | `min_pattern_coverage = 0,8` (anahtar adı değişti) | WP-4 S-1 |
| İzinli günde planlı saat bilinmiyorsa | Kalite 50 + not | WP-4 S-2 |
| Tünel +4 gün / grup içi kıdem (WP-3 ↔ WP-4 farkı) | +4 kuralı açılmaz, tünel projelerinde not gösterilir. Kıdemde kaynak `leave_seniority_date` esas; grup içi geçişler sayı olarak görünür | WP-4 incelemesi D |
| Planlı bitiş dışlaması | `verified` modu; zincirleme kısa sözleşmeler lineage'da sayılır | WP-5 S-1, S-2 |
| SGK kodları 23–25 / 13 | 23–25 `voluntary` + `voluntary_just_cause` kırılımı; 13 → `other` | WP-5 S-3 |
| KPI-020 bilinmeyen kategori bantları | 0,05 / 0,20; düşük bilgi payı uyarısı (unknown + other) için > %15 | WP-5 S-4, koşul 1 |
| Besleme kapsamı kapısı | Genel 0,8, İSG 0,9; İSG için hemen `enforce`, diğerleri `warn` → 3 ay sonra `enforce` (görünür değişiklik → PO-6) | WP-6 veri S-1, S-2 |
| Eksik beslemeli dönem kapatma | Engel değil, kapanış kaydında liste | WP-6 veri S-3 |
| Hukuk gönderim sırası | Paket 1 / 2 / 3 (§6); SUB-03 aciliyeti A'ya yükseltildi | WP-3 §11 |

### 3. Terim ve adlandırma kararları (tek sözlük)

- **Dosya adlandırma:** teslim edildiği gibi **`docs/hr/<alan>/WP-<n>_<konu>.md`**. `HR_TEAM_PLAN.md` ve persona brief'lerindeki `HSE-xx` / `DATA-xx` / `LEGAL-xx` adlarının yerini bu kural alır (eşleme: HSE-01 = WP-1, HSE-02 = WP-2, LEGAL-01 = WP-3, DATA-01 = WP-4, DATA-02 = WP-5, DATA-03 = WP-6 veri + WP-6 İSG). Bir WP iki alana bölünürse aynı numara klasör ve konu adıyla ayrılır. Sonraki sprintler WP-7'den devam eder; hukuk dosyaları `WP-<n>_legal_question_file_02.md` biçimindedir. Gerekçe: yeniden adlandırma çapraz referansları ve inceleme izini bozar; `TASK_BOARD.md` WP numarasıyla izler. Plan ve persona metinlerini Orchestrator hizalar.
- **İzin türleri:** `annual`, `sick`, `work_injury` (yeni), `parental`, `unpaid`, `unexcused`, `public_holiday`, `other`. "Raporlu istirahat" ailesi = {`sick`, `work_injury`}. Uzun yokluk (KPI-056) = {`sick`, `parental`, `unpaid`, `work_injury`}, **tek sabitte** tanımlanır. D-6 önceliği (`parental` önce) değişmez.
- **Kapsam (B-20):** tablo `data_feed_coverage`. Alanlar: `period_start` / `period_end`, `completeness ∈ {complete, partial, none}` (satır yoksa `undeclared`, `none` gibi davranır), `confirmed_empty`, `attributes` (İSG için `includes_subcontractor_cases`, `includes_near_miss`). `feed_type = safety`, import tipi `safety_event`. "Veri yok" → `insufficient_data`; gerçek sıfır yalnızca `complete` (+ boşsa `confirmed_empty`) ile.
- **Çalışan kategorisi:** `own_employee`, `subcontractor_worker`, `temporary_agency_worker`, `third_party`.
- **Yeniden sınıflandırma kodları (izin ve İSG için ortak):** `reclassified_up`, `reclassified_down`, `leave_reclassified_protected`, `leave_label_code_conflict`, `leave_label_ambiguous`, `unmapped_sgk_report_code`.
- **Renk bastırma:** tek mekanizma. `threshold_status = null` + `status_reason ∈ {short_window, low_exposure, unknown_category_share, low_coverage}`. WP-5'in önerdiği ayrı `threshold_suppressed` bayrağı açılmaz.
- **Parametre anahtarları:** her anahtarın tek anlamı vardır ve hesaplayıcı başına beyaz listeye tabidir. `min_coverage` (KPI-004, üretken saat), `min_pattern_coverage` (KPI-055/056), `min_feed_coverage` + `required_feeds` (kapsam kapısı), `threshold_min_days`, `threshold_min_exposure_hours`, `planned_end_exclusion`, `denominator_basis`.

**Çözülen çapraz tutarsızlıklar:** dosya adları; kapsam alan adları; yeniden sınıflandırma kodları; `min_coverage` anahtarının iki anlamda kullanılması; iki ayrı renk bastırma bayrağı; WP-5 CSV'deki %10 ile belgedeki %15; OHS-07 görünürlüğü ile WP-2 Q7 arasındaki fark; AL-02/AL-04 güvenli varsayılan farkları; tanımsız `hazard_class` (sprint 2'de Agent 18 tanımlayacak); WP-1 kabul kriteri 11 ile Orchestrator'ın geçici test satırları; kod 11'in saklanmaması ile tutarlılık kontrolü arasındaki çelişki (tüzel kişilik × ay sayacıyla çözüldü); WP-3'teki %77,8 renk etiketi.

## 4. Ürün sahibi kararı gerektirenler

| # | Karar | Seçenekler | Önerim | Sonuç |
|---|---|---|---|---|
| **PO-1** | **D-3 revizyonu: KPI-003 ve `work_injury`** (WP-1) | A: dahil (D-3'ün lafzı) · B: hariç · C: hariç + eşiksiz "İş Kazası/Meslek Hastalığı İstirahat Payı" | **C** | Hiçbir eşikli devamsızlık göstergesinde kaza istirahati kalmaz, kapasite kaybı yine görünür. KPI-003 kaza istirahati kadar düşer (Örnek 1: %7,30 → %6,50). Yeni formül versiyonu açılır, eski sonuçlar korunur, `DECISIONS.md`'ye yeni satır girer. **Karar gelmezse:** WP-1 paketi KPI-003'te `work_injury`'yi açıkça **dahil ederek** (A) çıkar; sessiz dışlama olmaz |
| **PO-2** | **D-1 revizyonu: alt yüklenici görünürlüğü** (doğrulama notu b; WP-3 SUB-01/02/03) | (a) Proje kapsamı: bugünkü davranış (tüm projeler) / ilişkinin projesi. (b) Kademe: yayılım (bugün) / yalnızca doğrudan kademe / 2. kademe ve ötesi yalnızca toplu. (c) Geçmiş: ilişki öncesi dahil tüm geçmiş (bugün) / izin tarihinden itibaren / ilişki dönemleri | (a) **ilişkinin projesi** — bunu D-1 değişikliği değil **hata düzeltmesi** sayıyorum, çünkü D-1 projeler arası açılımı hiç karara bağlamadı; ürün sahibinin onayı yeterli. (b) **2. kademe ve ötesi yalnızca toplu**. (c) **ilişki dönemleri** (D-1'in "geçmiş dönemleriyle" amacı korunur, ilişki öncesi dönem açılmaz) | Ana yüklenicinin bazı görünümleri daralır; `test_hierarchy_change_over_time` gibi testler güncellenebilir. `AccessContext` ADR'si gerekir (ADR-c). Alt yüklenici verisi içeren gerçek import'ların ön koşuludur. Hukuki teyit: SUB-01..04 |
| **PO-3** | **Paylaşım izni olmadan alt yüklenici ölümlü kazası** (D-1 istisnası; WP-2 Q4, OHS-07(d)) | Göster / gösterme | **Göster:** proje düzeyinde yalnızca sayı, firma adı yok | Ölümlü kaza hiçbir kapsamda gizlenmez. Kişi bilgisi yok. Hukuki teyit OHS-07 |
| **PO-4** | **Grup şirketleri arası kıdem** (WP-4 L-5, WP-3 AL-04) | A: yalnızca aynı tüzel kişilik · B: platform tüm grubu otomatik birleştirir · C: kaynak (bordro) `leave_seniority_date` esas; grup kıdeminin tanınması bir şirket politikası kararıdır | **C**; ayrıca şirketin grup içi rotasyondaki kıdemi yan hak olarak tanıyıp tanımayacağına karar verilmesi | Platform bireysel hakkın kayıt sistemi olmaz. Grup içi geçişler sayı olarak görünür. Grup kıdemi tanınırsa bordroya ve `leave_entitlement_rules`'a yazılır. Yasal asgari AL-04 yanıtıyla belirlenir |
| **PO-5** | **Sağlık türevli KPI'lara erişim** (KPI-028/053/054 + yeni istirahat payı; WP-1 Q4, WP-3 KV-04/PL-04) | A: `kpi.read` (bugün) · B: ayrı izin (İK Direktörü, İK Müdürü, İSG Uzmanı) · C: B + proje müdürü yalnızca tüzel kişilik düzeyinde | **B** (KVKK yanıtına kadar) | Proje/departman müdürü, salt okur yönetici ve alt yüklenici yöneticisi bu göstergeleri göremez. İzin sözleşmesi ADR'si gerekir. KV-04 yanıtıyla yeniden bakılır |
| **PO-6** | **Kapsam kapısının devreye alınması** (WP-6 veri S-2) | Hepsi hemen `enforce` / hepsi `warn` / karma | **İSG hemen `enforce`; diğerleri `warn`, 3 ay sonra `enforce`** | Panolarda "veri eksik" durumu görünür hale gelir. Bu görünür bir ürün değişikliğidir ve kullanıcılara duyurulmalıdır. İSG'de yanlış "sıfır kaza" ortadan kalkar |
| **PO-7** | **Platform çıktılarının ücret, prim, devam primi, bireysel değerlendirme ve disiplinde kullanılmaması** (WP-3 PAY-01..05, §5 S8) | Yazılı politika / politika yok | **Yazılı politika + pano dipnotu** (şirket İK yönetimiyle birlikte) | Eşiklerin güvenli olmasının koşuludur (bildirimi caydırmama). Hukuki teyit PAY-01/02/05 |

**Bilgi için (ürün kararı değil, İK kararı):** TRIR renk için minimum maruziyet 500.000 sa'e çıktı. KPI-014A/B eşiklerinin "aktif" sayılması KPI-043'ün aktifleşmesine bağlandı. D-5 eşik değerleri değişmedi.

## 5. Mühendislik için önerilen uygulama sırası

Her paket küçük, tek amaçlıdır; kabul testi kaynak belgenin el hesaplarıdır. ADR numaralarını Orchestrator verir (aşağıdaki harfler yer tutucudur).

**ADR'ler:** **ADR-a** renk bastırma nedeni (`status_reason`) + `alerts` (ölümlü kaza) — `KpiResult` için geriye uyumlu ekleme. **ADR-b** izin türü kümesine `work_injury` + izin yeniden sınıflandırma kuralı. **ADR-c** `AccessContext`: proje kapsamlı alt yüklenici görünürlüğü, kademe ve geçmiş kuralı. **ADR-d** formül parametre sözleşmesi: hesaplayıcı başına beyaz liste + yeni anahtarlar. **ADR-e** veri kapsamı meta verisi ve kapı (`data_feed_coverage`, upload `coverage`, `required_feeds`, `min_feed_coverage`). **ADR-f** `SafetyEvent` vaka düzeyi şeması + `safety_event` import'u. **ADR-g** istihdam import'una SGK çıkış kodu, `planned_end_date`, `contract_type = unknown` + `termination_code_map`. **ADR-h** çalışma düzeni, resmî tatil, hak kuralları + izin günü (`days`, `day_basis`).

| Sıra | Paket | Kaynak | Uygulayan | Bağımlılık / engel | ADR | Kabul testi (el hesabı) |
|---|---|---|---|---|---|---|
| 0 | Türkçe güvenli katlama | WP-1 bulgusu | Orchestrator | — | — | **Tamamlandı** |
| **Dalga 1 — yeni tablo yok, en yüksek güvenlik/ifşa değeri** | | | | | | |
| 1 | Parametre beyaz listesi (okunmayan parametre reddedilir) | WP-5 M2 | A09 | — | d | `test_unread_formula_parameter_rejected_per_calculator` |
| 2 | Alt yüklenici görünürlüğü `project_id` ile sınırlanır | Doğrulama b, SUB-02 | A11 | PO-2(a) onayı | c | `test_project_scoped_sharing_hides_other_projects` |
| 3 | İSG R1 minimum maruziyet + R2 ölümlü kaza uyarısı (kategorisiz) + "kazasız gün" sayacı yok | WP-2 R1/R2 | A09, A07 | 1 | a | WP-2 A–H (H artık renksiz), I, J, L |
| 4 | KPI-002 `threshold_min_days: 360` | WP-5 §3.6 | A09 | 1 | — (yalnızca parametre) | WP-5 R1–R3 |
| 5 | `work_injury` eşlemesi + SGK 1/2 + KPI-028/053 dışlama + KPI-055/056 uzun yokluk listesi + KPI-003 açık parametre | WP-1 §3.2–3.3 | A08, A09, A05 | PO-1 (gelmezse A ile çıkar) | b | WP-1 Örnek 1, 1b, 3; katlama testindeki 5 satır güncellenir |
| 6 | İzin yeniden sınıflandırma (yerinde güncelleme, {sick, work_injury} örtüşmesi hata, yön sayaçları) | WP-1 §3.4 | A08 | 5 | b | WP-1 Örnek 2 |
| 7 | Alt yüklenici kademe ve geçmiş kuralı | SUB-01/03 | A11 | PO-2(b)(c) | c | `test_tier2_subcontractor_aggregate_only`, `test_subcontractor_history_limited_to_relationship_periods` |
| **Dalga 2 — veri temeli** | | | | | | |
| 8 | Kapsam meta verisi çekirdeği (tablo, upload beyanı, başlık-yalnız dosya, kapı; mevcut `employment`/`leave` için) | WP-6 veri §3.4–3.6 | A05, A08, A09, A07 | 1; PO-6 | e | WP-6 veri K6, K7, K9 |
| 9 | `safety_event` import'u (vaka düzeyi, `worker_category`, yasaklı sütunlar, kapsam beyanı) | WP-6 İSG | A05, A08, A11 | 2, 8 (gerçek veri için 7 de) | f | WP-6 İSG 4.1, 4.2, 4.3; K4/K5 |
| 10 | KPI-043 aktivasyonu + R2 kategori kırılımı + "oran dışı vaka" | WP-2 R3, WP-6 İSG §3.7 | A09, A07 | 9 | — (katalog) | WP-2 K, M–P |
| 11 | İstirahat payı KPI'sı + breakdown maskeleme + geçmiş veri taşıma (açık dönemler) | WP-1 §3.3 C, §3.6, §3.7 | A09, A11, A05 | 5; PO-1, PO-5 | b | WP-1 Örnek 1 (%0,80), kabul kriteri 9, 12 |
| 12 | Puantaj import'u (`day_type`, `period_days`, satırlar arası kontroller) | WP-6 veri §3.2 | A08, A05 | 8 | e | Puantaj örnek dosyası |
| 13 | İstihdam import'u genişlemesi (SGK çıkış kodu, `planned_end_date`, `unknown` sözleşme, doğrulanmamış eşleme tablosu etkin değil) | WP-5 §3.1 | A05, A08, A11 | 1 | g | `test_blank_contract_type_stored_as_unknown_not_permanent`, `test_health_related_exit_codes_not_stored` |
| 14 | KPI-018/021/022 v2 (doğrulanmış planlı bitiş, dışlanan pay, alarm) + KPI-020 bantları | WP-5 §3.3–3.5 | A09 | 13, 3 (ADR-a) | a, d | WP-5 W1–W6, V1–V6 |
| 15 | Çalışma düzeni + resmî tatil + izin günü çevrimi (import) | WP-4 §3.1–3.2 | A05, A08 | 8 | h | WP-4 E5, E6, E12 |
| 16 | KPI-055/056 yeni versiyon (gün tabanlı pay, sözleşmesel hak, kıdem birleşimi, `min_pattern_coverage`) | WP-4 §3.3–3.4 | A09 | 15, 5 | d, h | WP-4 E1–E4, E7, E8, M3a |
| 17 | KPI-003/028/053/054 `denominator_basis` (gün düzeyinde geri ekleme) | WP-4 §3.5 + koşul 2 | A09 | 12, 15 | d | WP-4 E9–E11 |
| 18 | Eğitim import'u (`legal_basis`, `is_statutory_hse`, kırılım) | WP-6 veri §3.3 | A08, A09 | 8 | e | K1–K3 |
| 19 | Maliyet import'u (+ maskeli önizleme) | WP-6 veri §3.7 | A08, A11 | 8 | e | K8, maliyet örnek dosyası |
| **Dalga 3 — gerçek veri biriktikten sonra** | | | | | | |
| 20 | Bildirim kalitesi sinyalleri S1–S7 + izin ↔ olay uyum sinyali | WP-2 R4, WP-1 §3.5 | A09, A11 | 9, 6; 12 aylık veri | a | WP-2 §4.5, WP-1 Örnek 4 |

Paralel çalışabilenler: 1 ‖ 2 ‖ 4; 3 ‖ 5; 12 ‖ 13 ‖ 15 (8'den sonra). WP-3 için mühendislik işi yoktur. Her paketten sonra demo veride salt okunur kabul kontrolü yaparım (HR_TEAM_PLAN §4.5).

## 6. İnsanlara gidecekler

**Hukuk danışmanı (TR) — WP-3 gönderim paketleri:**
- **Paket 1 (A, hemen):** SUB-01, SUB-02, SUB-03 (A'ya yükseltildi), SUB-04, KV-01, KV-02, KV-04, PL-04, OHS-01, KV-05…KV-09, KV-RU-01 (yerel avukat).
- **Paket 2 (B):** OHS-03, OHS-07 (güvenli varsayılan WP-2 Q7'ye göre hizalanmış), OHS-08, OHS-09, OHS-10, KV-03, AL-02, AL-03, AL-04, AL-06, AL-14, WT-01/02/04/07, PL-01/02/03, PAY-01/02/05. Dosya no. 2'den: **L-8 (sağlık ima eden SGK çıkış kodları, aciliyet A)**, L-9…L-12 (WP-5), L-13…L-17 (WP-6 veri), 4857 md. 7 geçici iş ilişkisi işçileri ve kimliksiz vaka satırının belirlenebilirliği (WP-6 İSG).
- **Paket 3 (C):** kalanlar ve iş kararı alınmış, yalnızca "engel var mı" teyidi istenen sorular: OHS-02, PAY-03(a), PAY-04.

**KVKK irtibat kişisi:** KV-01…KV-09, KV-03 (minimum grup + fark alma), L-8, L-13, OHS-07(b)(c), PO-5'in rol listesi, `work_injury` işleme dayanağı.

**İş güvenliği uzmanı / işyeri hekimi (6331 md. 8):** OHS-01 (gerçek dosyada SGK rapor türü kodları), OHS-03, OHS-10 (servis kazası, işle ilgisiz ölüm, ramak kala yükümlülüğü), gerçek İSG sınıf etiketlerinin WP-6 İSG §3.4 eşlemesi ve LTI gün tanımı, H_min ve S1–S5 değerlerinin prosedüre alınması, L-14 / OHS-04 (eğitim süreleri, tehlike sınıfı), WT-04 / AL-02 (tünel iş kalemleri).

**İSG Kurulu (6331 md. 22) — çalışan temsilcisiyle:** WP-1 (iş kazası türü, yeniden sınıflandırma sayaçları), WP-2 (renk korkulukları, KPI-043 `neutral`, ölümlü kaza bandı) devreye alınmadan önce görüşülür (OHS-08).

**Yerel avukat (RU ve diğer ülkeler):** WT-RU-01/02, AL-RU-01, KV-RU-01; ülke listesine göre çoğaltılır.

### Gerçek organizasyondan ihtiyacım olanlar

1. Gerçek kaynak sistemlerin adları ve örnek dışa aktarımları: puantaj (günlük mü, aylık cetvel mi), izin/bordro, LMS, İSG kayıt sistemi.
2. Gerçek dosyalarda **SGK rapor türü kodları** ve **güncel işten çıkış kodu listesi**; satır satır doğrulayacak bordro/SGK uygulayıcısı (`verified_by`).
3. Şantiye/kişi bazında çalışma düzenleri (5/6 gün, günlük saat, geçerlilik tarihi) ve ülke bazında resmî tatil takvimleri.
4. TİS ve sözleşmelerdeki ek izin günü kuralları, kaynak belge numaralarıyla; İK yönetiminin onayı.
5. İşyeri/proje bazında tehlike sınıfı; tünel / yer altı proje ve iş kalemi listesi.
6. Tüzel kişilik ve ülke listesi (yurt dışı şantiyeler); grup içi rotasyon politikası (PO-4).
7. 24–36 aylık geçmiş veri ve açılış izin bakiyeleri (kalibrasyon ve 12 aylık pencereler için).
8. Alt yüklenici sözleşmelerinde veri paylaşımı ve İSG/puantaj bildirim hükümleri (satın alma); paylaşım izinlerinin sözleşme kanıtı (SUB-04).
9. PO-7 kullanım politikasının İK yönetimi tarafından yazılı olarak benimsenmesi ve çalışanlara duyurulması.

## 7. Sonraki adımlar

- Agent 18/19/20 koşulları belgelerine işler (yeniden inceleme yalnızca koşul maddeleri için yapılır). Agent 20 dosya no. 2'yi sprint 2 başında hazırlar.
- Orchestrator: §5 paketleri için `TASK_BOARD.md` kayıtları ve ADR-a…h; PO-1…PO-7'yi ürün sahibine iletir; `HR_TEAM_PLAN.md` ve persona brief'lerindeki dosya adı kuralını §3'e göre hizalar.
- Sprint 2 adayları (yeni): `hazard_class` alanı (Agent 18), KPI-002'de planlı demobilizasyon (Agent 19), rotasyon çalışma düzeni (WP-3 yanıtlarından sonra), alt yüklenici çalışanları için ayrı İSG oranı, `occupational_illness` olay türü, puantaj cetveli geniş formatı.

---

## Summary (English)

**Verdicts:** all 7 deliverables are **conditionally approved**; none returned. WP-1 (7 conditions), WP-2 (7), WP-6 HSE (6), WP-4 (4), WP-5 (7), WP-6 data (6), WP-3 (6). Package 1 of the legal file can go to counsel now. I recomputed every hand-worked example and they all match, with two exceptions: WP-3's AL-02 example has the wrong colour label (77.8 % is `good` in the engine, not "warning"), and the WP-5 CSV threshold (10 %) contradicts the spec (15 %). Cross-document conflicts I resolved: file naming (keep `WP-<n>_<topic>.md`), coverage field names, reclassification rule codes, the `min_coverage` key being used in two senses, a single colour-suppression mechanism (`status_reason`), and the safe-default differences between WP-3 and WP-4.

**Product-owner decisions needed:** PO-1 D-3 revision, KPI-003 excludes `work_injury` (I recommend option C); PO-2 D-1 revision, subcontractor visibility scoped to the project (I treat this as a defect fix), tier 2+ aggregate only, history limited to relationship periods; PO-3 subcontractor fatalities visible without a sharing grant (count only, no firm name); PO-4 seniority across group companies (payroll seniority date governs; group recognition is a company policy choice); PO-5 a dedicated permission for health-derived KPIs; PO-6 coverage gate rollout (HSE enforced now, other feeds warn then enforce); PO-7 a written policy that platform outputs are never used for pay, bonus or individual evaluation.

**Implementation order:** Wave 1: parameter whitelist → project-scoped subcontractor access → HSE minimum exposure + fatality alert → KPI-002 12-month rule → `work_injury` mapping/KPIs → leave reclassification → subcontractor tier/history rule. Wave 2: coverage metadata → HSE import → KPI-043 → rest-share KPI → timesheet import → exit-code import → turnover v2 → work patterns → KPI-055/056 → absence denominator → training → cost. Wave 3: reporting-quality signals once 12 months of real data exist. ADRs a–h are listed in §5.
