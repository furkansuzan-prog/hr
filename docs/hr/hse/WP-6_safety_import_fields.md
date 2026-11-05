# WP-6 (İSG bölümü) — İSG olay import'u: alan sözlüğü, sınıflandırma eşlemesi ve doğrulama kuralları

> **Statü: Taslak — İK Direktörü incelemesi bekliyor.** Hazırlayan: Agent 18 (İSG Uzmanı). Onaylı değildir.
> Bu belge kod, katalog veya migration değiştirmez. Örnek satırlar kurgusaldır (firma/proje kodları uydurmadır).
> **Kişi kimliği ve tanı/sağlık ayrıntısı bu import'ta hiçbir biçimde yer almaz.**
> İlişki: `HR_TEAM_PLAN.md` WP-6'nın sahibi Agent 19'dur ve birleşik çıktı `docs/hr/data/DATA-03_import_specs_timesheet_training_cost_hse.md`'dir; bu belge onun **İSG alanları** girdisidir (Agent 19 DATA-03'e referansla alır; veri kapsamı meta verisi B-20 kuralının sahibi DATA-03'tür).

| | |
|---|---|
| Tarih | 2026-11-05 |
| Sahip / inceleyen | Agent 18 → Agent 17 → (Agent 19 DATA-03 ile birleştirme) → Orchestrator |
| Girdiler | `TASK_BOARD.md` B-07, B-20; `docs/AUDIT_REPORT.md` #19; `backend/app/models/operations.py` (`SafetyEvent`), `models/base.py` (`SourceMixin`); `backend/app/services/imports.py` (`FIELD_SETS`, `LEAVE_FIELDS`, `_validate_leave`, `_HEALTH_COLUMN_HINTS`, `_norm`, `suggest_mapping`); `backend/app/analytics/calculators.py` (`_safety_rate`); `docs/SECURITY_PRIVACY.md` (veri minimizasyonu); `docs/HR_DIRECTOR_RECOMMENDATIONS.md` §3.3, §6.1; WP-1, WP-2 (bu klasör) |
| Uygulayacaklar (öneri) | A08 (import tipi `safety_event`), A05 (yeni alanlar + migration), A09 (oran payı/paydası tutarlılığı), A07 (`/imports` ekranı), A12 |

---

## 1. Kapsam ve ilgili backlog ID'leri

- **B-07** (İSG import tipi açık), **B-20** / **AUDIT #19** (eksik besleme ≠ gerçek sıfır).
- WP-2 sinyallerinin ve ölümlü kaza uyarısının ihtiyaç duyduğu alanlar (`worker_category`, `reported_date`, vaka anahtarı).
- Kapsam dışı: puantaj/maruziyet saati import'u (Agent 19 DATA-03), eğitim (KPI-045), meslek hastalığı vakaları (v1'de desteklenmez — Q2).

## 2. Mevcut davranış (kod/doküman referansıyla)

- `SafetyEvent`: `legal_entity_id`, `project_id` (ops.), `event_date`, `event_type` (`fatality|lost_time_injury|restricted_work|medical_treatment|first_aid|near_miss`), `lost_days` (int, varsayılan 0), `SourceMixin` (`source_system`, `source_record_id`, `record_version`, `updated_by`). Model yorumu: "tanı veya kişi kimliği olmadan; özel nitelikli veri İSG kayıt sisteminde kalır".
- `imports.FIELD_SETS` yalnızca `employment` ve `leave`; İSG import tipi yok. Demo veri seed ile oluşturulmuş.
- `_safety_rate` her `safety_events` satırını **bir** olay sayar; çoklu yaralanmalı olayda LTI sayısı (vaka sayısı) eksik sayılabilir. Pay olayın `legal_entity_id`'sine, payda çalışanın istihdam firmasına göre filtrelenir → olay alt yüklenici firmasına yazılmış ama o firmanın puantajı yoksa tenant düzeyinde pay şişer, payda eksik kalır.
- İzin import'u `_HEALTH_COLUMN_HINTS` ile tanı benzeri sütunları **saklamadan önce** düşürür — bu desen burada genişletilerek kullanılır.
- Kapsam (besleme) meta verisi yok: olay olmayan ay "0" görünür (B-20).

## 3. Önerilen kural

### 3.1 Birim kayıt (granülerlik)

- **Bir satır = bir vaka.** Yaralanma/ölüm sınıflarında vaka = etkilenen bir kişinin sonucu (kimliği olmadan); 3 kişinin yaralandığı tek olay 3 satırdır (`event_ref` aynı, `case_no` 1–3). LTIFR/TRIR **vaka** sayar (OSHA 1904 ve GRI 403-9 vaka/kişi bazlıdır — teyit edilmeli).
- Ramak kala: bir satır = bir bildirim (`case_no` boş/1).
- Kaynak anahtarı: `(tenant, source_system, event_ref, case_no)` → `source_record_id = "<event_ref>#<case_no>"`.
- Sınıf, vakanın **en ağır sonucu**dur: ölümlü > LTI > kısıtlı iş > tıbbi müdahale > ilk yardım. Sonuç ağırlaşırsa (ör. LTI vakası sonradan ölüm) aynı anahtarla güncellenir.

### 3.2 Sınıflandırma sözlüğü (platform `event_type`)

| `event_type` | Tanım (platform için) | Dayanak (teyit edilmeli) |
|---|---|---|
| `fatality` | İşle ilgili yaralanma sonucu ölüm (olay anında veya sonradan) | GRI 403-9; ESRS S1-14; 5510 md. 13 ⚖️ |
| `lost_time_injury` (LTI) | Olay gününden sonra **en az bir tam takvim günü** işe gelememe ile sonuçlanan işle ilgili yaralanma (ölüm hariç) | OSHA 29 CFR 1904.7(b)(3) "days away"; IOGP/ILO pratiği |
| `restricted_work` (RWC) | İşe gelememe yok; ancak olay gününden sonra olağan işin tamamını yapamama veya başka göreve geçici geçiş | OSHA 1904.7(b)(4) |
| `medical_treatment` (MTC) | İlk yardımın ötesinde tıbbi müdahale (ör. dikiş, reçeteli ilaç, sabitleme), gün kaybı/kısıt yok | OSHA 1904.7(b)(5) |
| `first_aid` (FAC) | Yalnızca ilk yardım listesindeki müdahaleler (yara bandı, soğuk uygulama, göz yıkama vb.) | OSHA 1904.7(b)(5)(ii) |
| `near_miss` | Yaralanma/hastalık/ölüm olmadan, zarar potansiyeli taşıyan olay | 6331 md. 14(1)(b) ⚖️; ISO 45001 10.2 |

Türk mevzuatında "iş kazası" (5510 md. 13) **ağırlık sınıfı değildir**; SGK'ya bildirilen bir iş kazası LTI, RWC, MTC veya FAC olabilir. Bu yüzden kaynakta yalnızca "iş kazası" yazan satırlar §3.4'teki türetme kuralına tabidir.

### 3.3 Alan sözlüğü (`entity_type = "safety_event"`)

| Alan | Zorunluluk | Tip / birim | Açıklama | Kaynak başlık eşanlamlıları (öneri, `suggest_mapping`) |
|---|---|---|---|---|
| `source_system` | Ops. (vars. `import`) | metin ≤ 50 | Kaynak İSG sistemi adı | kaynak, source, система |
| `event_ref` | **Zorunlu** | metin ≤ 100 | Kaynak sistemdeki olay no | olay no, olay numarası, kaza no, kayıt no, incident no, incident id, event id, номер происшествия |
| `case_no` | Koşullu (aynı olayda > 1 vaka varsa zorunlu) | tamsayı ≥ 1, vars. 1 | Olay içindeki vaka sıra no — **kişi kimliği değildir** | vaka no, vaka sıra, case no, case seq |
| `legal_entity_code` | **Zorunlu** | kod | Yaralanma sınıflarında **etkilenen çalışanın işvereni**; ramak kala ve üçüncü kişi vakalarında işi yürüten firma | firma, firma kodu, işveren, alt yüklenici kodu, company, employer, компания |
| `project_code` | Önerilen (boşsa uyarı) | kod | Şantiye/proje | proje, şantiye, project, site, проект, объект |
| `event_date` | **Zorunlu** | tarih | Olay tarihi | olay tarihi, kaza tarihi, tarih, event date, incident date, дата происшествия |
| `classification` | **Zorunlu** | metin/kod | Kaynak sınıf etiketi → `event_type` (§3.4) | sınıf, olay sınıfı, olay türü, kaza türü, sonuç, classification, incident type, severity, категория |
| `lost_days` | Koşullu (LTI'da zorunlu, vaka açıksa boş olabilir) | tamsayı ≥ 0, **takvim günü** | Olay günü hariç, işe dönüşe kadar işe gelinemeyen takvim günleri (§3.5) | kayıp gün, kayıp iş günü, iş günü kaybı, istirahat günü, lost days, days away, lost workdays, потерянные дни |
| `restricted_days` | Ops. | tamsayı ≥ 0, takvim günü | Kısıtlı iş/görev değişikliği gün sayısı (S4 sinyali için yararlı) | kısıtlı iş günü, hafif iş günü, restricted days, days restricted |
| `worker_category` | Yaralanma sınıflarında **zorunlu**; ramak kalada yok | enum | `own_employee` · `subcontractor_worker` · `temporary_agency_worker` · `third_party` | çalışan türü, personel tipi, işçi kategorisi, worker type, worker category, категория работника |
| `reported_date` | Önerilen | tarih | Olayın şirket İSG sistemine **iç kayıt** tarihi (S5) | iç bildirim tarihi, kayıt tarihi, sisteme giriş tarihi, reported date, date reported, дата регистрации |
| `case_status` | Ops. | `open` / `closed` | Kayıp gün hâlâ artıyor mu | vaka durumu, durum, case status, status |
| `sgk_notification` | Ops. | `notified` / `pending` / `not_required` | Kaynak sistemin SGK bildirim durumu — **platform karar vermez, yalnızca toplu sayar** | sgk bildirimi, sgk durumu, sgk notification |
| `sgk_notification_date` | Ops. | tarih | SGK bildirim tarihi | sgk bildirim tarihi, sgk tarihi |
| `high_consequence` | Ops. | evet/hayır | GRI 403-9 "ağır sonuçlu" (6 ayda iyileşme beklenmeyen) | ağır sonuçlu, high consequence |

Model etkisi (A05): `worker_category`, `case_no` (veya birleşik `source_record_id`), `reported_date`, `restricted_days`, `case_status`, `sgk_notification`, `sgk_notification_date`, `high_consequence` yeni sütunlardır → yeni Alembic revizyonu. `event_type` değer kümesi değişmez.

`worker_category` değer eşlemesi:

| Kaynak değer (katlanmış alt dize) | Hedef |
|---|---|
| kendi personeli, asıl işveren, kadrolu, direct, own, employee, штатн | `own_employee` |
| taşeron, alt yüklenici, alt işveren, subcontractor, contractor, подрядчик, субподряд | `subcontractor_worker` |
| geçici iş ilişkisi, ödünç işçi, agency, temporary, временн | `temporary_agency_worker` ⚖️ (4857 md. 7) |
| ziyaretçi, üçüncü şahıs, üçüncü kişi, halk, visitor, public, third party, посетител | `third_party` |
| diğer / tanınmayan | **hata** `unknown_worker_category` |

### 3.4 Sınıf etiketi eşlemesi

Kurallar:
1. Etiket `imports._norm` ile katlanır (Türkçe güvenli: "İLK YARDIM" = "ilk yardim").
2. **Kısa kodlar** (`FAT`, `LTI`, `LTC`, `LWC`, `DAFW`, `RWC`, `RWI`, `MTC`, `MTI`, `FAC`, `FA`, `NM`) yalnızca **tüm değer** veya ayrı sözcük olarak eşleşir (ör. `FA` "FATAL" içinde eşleşmez).
3. Sözcük iğneleri alt dize olarak aranır. **Birden fazla sınıf eşleşirse hata** (`classification_ambiguous`) — platform tahmin etmez.
4. Hiçbir sınıf eşleşmezse §3.4.b türetme kuralı, o da yoksa hata `unmapped_classification`. **Varsayılan sınıf yoktur** (izin import'undaki `other` gibi bir sepete düşmez).

**a) Sözlük**

| `event_type` | TR iğneleri | EN iğneleri / kodlar | RU iğneleri |
|---|---|---|---|
| `fatality` | ölümlü, ölüm, vefat | fatal, fatality, death · `FAT` | смертельн, летальн, гибел |
| `lost_time_injury` | kayıp günlü, kayıp iş günlü, iş günü kayıplı, istirahatli kaza | lost time, lost workday, days away · `LTI`, `LTC`, `LWC`, `DAFW` | с потерей трудоспособности, временной нетрудоспособност |
| `restricted_work` | kısıtlı iş, hafif iş, görev değişikliği | restricted work, job transfer, light duty · `RWC`, `RWI` | ограниченн, легкий труд, лёгкий труд |
| `medical_treatment` | tıbbi müdahale, tıbbi tedavi | medical treatment · `MTC`, `MTI` | медицинское лечение, медицинская помощь *(yalnızca "первая" geçmiyorsa)* |
| `first_aid` | ilk yardım, ilkyardım | first aid · `FAC`, `FA` | первая помощь, первая медицинская |
| `near_miss` | ramak kala, ramakkala | near miss, near-miss, close call · `NM` | почти, предпосылк |

Not: RU "первая медицинская помощь" hem `first_aid` hem `medical_treatment` iğnesine değer → kural 3 gereği belirsiz olmaması için `medical_treatment` iğnesi "первая" içermeyen değerlerle sınırlıdır (A08 uygulama ayrıntısı).

**b) Genel "iş kazası" etiketleri (ağırlık yok)** — "iş kazası", "kaza", "yaralanma", "incident", "injury", "accident", "несчастный случай", "травма":

| Koşul | Sonuç |
|---|---|
| `lost_days ≥ 1` | `lost_time_injury` + uyarı `classification_derived_from_lost_days` |
| `lost_days` 0 veya boş | **hata** `severity_required` (MTC mi FAC mi bilinemez; tahmin yok) |
| Ölüm, kısıtlı iş, tıbbi müdahale, ilk yardım | **Asla türetilmez** |

**c) Desteklenmeyen / politika gerektiren etiketler → hata (veri kalite kuyruğu, insan kararı)**

| Etiket örnekleri | Kural kodu | Neden |
|---|---|---|
| meslek hastalığı, occupational disease/illness, профзаболевание | `illness_not_supported_v1` | `event_type` kümesinde hastalık yok; KPI-014B "injuries" tanımlı (Q2) |
| trafik kazası, servis kazası, yol kazası, commuting | `commuting_policy_required` | ⚖️ 5510 md. 13 işverence sağlanan taşıtla gidiş-gelişi kapsar; GRI 403-9 yalnızca organize taşımayı; OSHA işe gidiş-gelişi dışlar → politika kararı (Q3) |
| maddi hasar, çevre olayı, yangın (yaralanmasız), property damage, environmental | `out_of_scope_event` | Yaralanma/ramak kala değil; ramak kala sayılıp sayılmayacağına İSG uzmanı karar verir |

### 3.5 Kayıp gün sayımı

- **Takvim günü**, olay günü hariç, işe dönüş (veya hekim raporunun bittiği gün) dahil olmayan son güne kadar; hafta tatili ve resmî tatil **dahil** (OSHA 1904.7(b)(3) — teyit edilmeli).
- Açık vakada (`case_status = open`) değer sonraki import'larda güncellenir (aynı anahtar → güncelleme).
- **Saklanan değer kaynağın değeridir** (kırpılmaz). KPI-044 (Kayıp Gün Oranı, taslak) aktive edilirse vaka başına 180 gün tavanını hesapta uygular (OSHA tavanı — Q4).
- `fatality` için gün yazılmaz (0/boş); ANSI Z16.1'in ölüm başına 6.000 gün konvansiyonu **kullanılmaz** — ölümler ayrı sayılır (ESRS S1-14 ölümleri ve kayıp günü ayrı ister — teyit edilmeli).
- Bu değer WP-1 `work_injury` izin saatleriyle **kişi düzeyinde eşleştirilmez**; uyum yalnızca grup düzeyinde (WP-1 §3.5).

### 3.6 Veri kapsamı beyanı (B-20 — DATA-03 ile ortak)

- Olay olmayan aylar dosyada satır üretmez; bu nedenle her İSG batch'i **kapsam beyanı** taşır: `legal_entity_code`, ops. `project_code`, `coverage_from`, `coverage_to`, `includes_subcontractor_cases` (evet/hayır), `includes_near_miss` (evet/hayır). (Yükleme formunda veya "KAPSAM" adlı ikinci sayfada — biçim DATA-03 kararı.)
- Kural: kapsam × ay için beyan **yoksa** İSG oranları ve KPI-043 `insufficient_data`; beyan **varsa** ve satır yoksa gerçek **0**. `includes_near_miss = hayır` ise KPI-043 o kapsam için `insufficient_data` (KPI-014A/B etkilenmez).

### 3.7 Pay–payda tutarlılığı (A09)

- `third_party` vakaları oranlara **hiç girmez** (paydada çalışma saati yok); ölümlü kaza uyarısına girer (WP-2 R2).
- `subcontractor_worker` / `temporary_agency_worker` vakası, vakanın `legal_entity_code` firması için o ay **puantaj kapsamı varsa** orana girer; yoksa orandan dışlanır, sonucun yanında **"oran dışı vaka: n"** sayısıyla gösterilir ve ölümlü uyarısına girer. Vaka hiçbir koşulda kaybolmaz.
- Bu, GRI 403-9'un "çalışan olmayan ama işi kuruluşça kontrol edilen işçiler" için ayrı oran istemesiyle uyumlu bir sonraki adıma (kategori bazlı oran) zemin hazırlar.

### 3.8 Doğrulama kuralları

| # | Kural | Ciddiyet | Kural kodu |
|---|---|---|---|
| V1 | Zorunlu alanlar dolu (§3.3) | hata | `required` |
| V2 | `event_date` geçerli tarih, ≥ 2000-01-01, **≤ import günü** | hata | `date_format`, `event_date_in_future` |
| V3 | `reported_date` ≥ `event_date` ve ≤ import günü | hata | `reported_before_event` |
| V4 | `sgk_notification_date` ≥ `event_date` | hata | `sgk_date_before_event` |
| V5 | `lost_days`, `restricted_days` tamsayı, 0–3650 | hata | `lost_days_range` |
| V6 | LTI → `lost_days ≥ 1`; boş ve `case_status = open` ise uyarı, boş ve kapalı/bilinmiyorsa hata | hata/uyarı | `lti_without_lost_days`, `lost_days_pending` |
| V7 | **RWC/MTC/FAC/NM ile `lost_days > 0` → hata** (olası düşük sınıflandırma; S6'ya sayılır) | hata | `lost_days_class_conflict` |
| V8 | `fatality` ile `lost_days > 0` → değer yok sayılır | uyarı | `fatality_lost_days_ignored` |
| V9 | RWC ve `restricted_days` = 0/boş | uyarı | `rwc_without_restricted_days` |
| V10 | Yaralanma sınıfında `worker_category` boş/tanınmayan | hata | `unknown_worker_category` |
| V11 | Firma var, kullanıcının `import.write` kapsamında (izin import'uyla aynı) | hata | `unknown_legal_entity`, `scope` |
| V12 | Proje kodu tenant'ta yok → hata; firma–proje ilişkisi yoksa uyarı | hata/uyarı | `unknown_project`, `entity_not_on_project` |
| V13 | Dosya içinde aynı `(source_system, event_ref, case_no)` | hata | `duplicate_in_file` |
| V14 | Aynı `event_ref`'in vakalarında `event_date` / `project_code` farklı | hata | `event_fields_inconsistent` |
| V15 | DB'de aynı anahtar varsa **güncelleme** (yeni satır değil); sınıf değişiyorsa yön (yukarı/aşağı) denetim izine ve S6 sayacına yazılır; `event_date` değişiyorsa hata (manuel) | bilgi/hata | `reclassified_up`, `reclassified_down`, `event_date_changed` |
| V16 | Daha önce yüklenmiş bir vaka sonraki tam dökümde **yoksa silinmez**; silme yalnızca İSG yetkili rolünün gerekçeli, denetimli manuel işlemiyle | — | — |
| V17 | Yasaklı sütunlar (§3.9) **saklanmadan önce** düşürülür; batch özetinde yalnızca sütun **adları** listelenir, değerler hiçbir yerde saklanmaz/yankılanmaz | uyarı (batch) | `prohibited_columns_dropped` |
| V18 | `classification` eşlenemedi / belirsiz / politika gerektiriyor (§3.4) | hata | `unmapped_classification`, `classification_ambiguous`, `severity_required`, `illness_not_supported_v1`, `commuting_policy_required`, `out_of_scope_event` |

Veri kalite kuyruğu mesajları satır numarası ve `event_ref` taşır; kişi bilgisi taşımaz (zaten yoktur).

### 3.9 Yasaklı sütunlar (kişi kimliği, sağlık, serbest metin)

Başlık katlanmış haliyle aşağıdaki alt dizelerden birini içerirse sütun düşürülür (mevcut `_HEALTH_COLUMN_HINTS` genişletilir):

| Grup | Alt dizeler (TR / EN / RU) |
|---|---|
| Kimlik | ad soyad, adi soyadi, isim, kazazede, yaralanan, yarali adi, tc, tckn, kimlik, sicil, personel no, employee id, employee number, name, passport, pasaport, telefon, phone, фамилия, имя, паспорт, табельный |
| Kişiyi tanımlayabilecek nitelik | yaş, yas, doğum, dogum, cinsiyet, gender, uyruk, nationality, meslek, occupation, görev, unvan, job title, возраст, пол, должность |
| Sağlık / tanı | tanı, tani, teşhis, teshis, diagnos, icd, yaralanma türü, yaralanma sekli, vücut bölgesi, vucut bolgesi, organ, body part, nature of injury, injury type, hastane, tedavi, treatment detail, диагноз, травма характер, часть тела |
| Serbest metin / kusur | açıklama, aciklama, olayın oluş, olay özeti, description, narrative, summary, not, notes, comment, kök neden, kok neden, root cause, kusur, sorumlu, fault, blame, tanık, witness, описание, причина, виновн |

Eşleme notu: kısa iğneler (`tc`, `not`, `yaş`/`yas`, `name`, `organ`, `имя`, `пол`, `görev`) yalnızca **ayrı sözcük** olarak eşleşir; aksi halde "SGK Notification" (`not`) veya "Organizasyon" (`organ`) gibi meşru başlıklar yanlışlıkla düşürülür. A12 bunun için negatif test yazar (`test_prohibited_column_short_tokens_whole_word`).

Not: "tedavi" yasaklıdır ama sınıf etiketi "tıbbi tedavi" `classification` **değeri** olarak geçerlidir — kural sütun **başlığına** uygulanır. Başlığı "Tedavi Türü" olan bir sınıf sütunu varsa kullanıcı onu `classification`'a eşlemelidir; eşlenmiş sütun yasak listesine takılırsa hata verilir ve sütunun yeniden adlandırılması istenir (yanlışlıkla tanı aktarımını önler).

## 4. El hesaplı örnekler (tablo)

### 4.1 Örnek dosya (kurgusal) ve beklenen sonuç — import günü 2026-11-05

Kaynak başlıklar: `Olay No | Vaka No | Firma | Proje | Olay Tarihi | İç Bildirim Tarihi | Olay Sınıfı | Kayıp Gün | Çalışan Türü | Vaka Durumu | Yaralanan Adı Soyadı`
Kapsam beyanı: FIRMA-A ve FIRMA-B, PRJ-01 ve PRJ-02, 2026-03-01 – 2026-03-31, alt yüklenici vakaları dahil, ramak kala dahil.

| # | Olay No | Vaka | Firma | Proje | Olay tarihi | İç bildirim | Sınıf (kaynak) | Kayıp gün | Çalışan türü | Beklenen |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | ISG-26-0141 | 1 | FIRMA-A | PRJ-01 | 2026-03-02 (Pzt) | 2026-03-02 | Kayıp Günlü (LTI) | 9 | Kendi personeli | ✔ `lost_time_injury`, 9 gün, `own_employee` |
| 2 | ISG-26-0142 | 1 | FIRMA-B | PRJ-01 | 2026-03-05 (Per) | 2026-03-12 (Per) | Tıbbi müdahale | 0 | Taşeron | ✔ `medical_treatment`, `subcontractor_worker`; S5 için "geç kayıt" (olay günü hariç 5 iş günü > 3) — satır hatası değil, toplu sinyal girdisi |
| 3 | ISG-26-0150 | — | FIRMA-A | PRJ-02 | 2026-03-09 | 2026-03-09 | Ramak kala | — | — | ✔ `near_miss`, `case_no` = 1 |
| 4 | ISG-26-0155 | 1 | FIRMA-A | PRJ-01 | 2026-03-10 | 2026-03-10 | İlk yardım | 4 | Kendi personeli | ✖ **hata** `lost_days_class_conflict` (ilk yardım + 4 kayıp gün → olası düşük sınıflandırma; S6 sayacı) |
| 5 | ISG-26-0158 | 1 | FIRMA-A | PRJ-01 | 2026-03-16 | 2026-03-16 | İş kazası | 3 | Kendi personeli | ✔ `lost_time_injury` + uyarı `classification_derived_from_lost_days` |
| 6 | ISG-26-0159 | 1 | FIRMA-B | PRJ-02 | 2026-03-18 | 2026-03-18 | İş kazası | 0 | Taşeron | ✖ **hata** `severity_required` |
| 7 | ISG-26-0163 | 1 | FIRMA-A | PRJ-01 | 2026-12-01 | 2026-12-01 | Ramak kala | — | — | ✖ **hata** `event_date_in_future` |
| — | (sütun) | | | | | | | | | `Yaralanan Adı Soyadı` **düşürüldü**, batch uyarısı `prohibited_columns_dropped` (yalnızca başlık adı) |

Sonuç: 7 satır → 4 geçerli (1, 2, 3, 5), 3 hatalı (4, 6, 7). Hatalı satırlar commit edilmez, veri kalite kuyruğunda düzeltilmeyi bekler.

### 4.2 Oran etkisi (aynı ay, kurgusal puantaj)

Mart 2026, PRJ-01 + PRJ-02: FIRMA-A puantajı 70.000 sa; **FIRMA-B puantajı yok** (kapsam beyanı İSG için var, puantaj beslemesi yok).

| Gösterge | Pay | Payda | Değer | Not |
|---|---|---|---|---|
| LTIFR (FIRMA-A + FIRMA-B, tenant) | Satır 1, 5 = 2 LTI (FIRMA-A) | 70.000 | 2 / 70.000 × 10⁶ = **28,57** | Renk yok (WP-2 R1; 70.000 < 1.000.000) |
| TRIR | 2 LTI (FIRMA-A); satır 2 MTC (FIRMA-B) **oran dışı** | 70.000 | 2 / 70.000 × 200.000 = **5,71** | Renk yok; "oran dışı vaka: 1 (puantaj kapsamı yok)" gösterilir |
| Bugünkü kod davranışı (karşılaştırma) | 3 kaydedilebilir (FIRMA-B dahil) | 70.000 | 3 / 70.000 × 200.000 = 8,57 | Pay ve payda farklı nüfuslardan |
| KPI-043 | Satır 3 = 1 | 70.000 | 1 / 70.000 × 200.000 = **2,86** | "Düşük maruziyet" |

### 4.3 Sınır durumu — çok vakalı olay ve sonradan ağırlaşma

| Adım | Girdi | Sonuç |
|---|---|---|
| Nisan import'u | `ISG-26-0201` vaka 1 = LTI (6 gün, açık), vaka 2 = MTC, vaka 3 = FAC; hepsi aynı tarih/proje | 3 vaka; LTIFR payına **1**, TRIR payına **2** (LTI + MTC), ilk yardım hiçbir orana girmez. Bugünkü "bir satır = bir olay" yaklaşımında tek satır yazılsaydı TRIR payına yalnızca 1 girerdi |
| Mayıs import'u | Aynı anahtar `ISG-26-0201#1`, sınıf "Ölümlü" | Güncelleme (yeni satır yok), `reclassified_up`; LTIFR payı değişmez (ölümlü de LTIFR payında), **ölümlü kaza uyarısı açılır** (WP-2 R2) |
| Haziran import'u | Aynı anahtar `ISG-26-0201#2`, sınıf "İlk yardım" (MTC'den düşürülmüş) | Güncelleme, `reclassified_down` → S6 sayacı +1; TRIR payı 2 → 1 (Nisan yeniden hesaplanır; kapanmışsa dondurulmuş değer korunur ve not düşülür) |
| Haziran tam dökümünde `ISG-26-0201#3` yok | — | **Silinmez** (V16) |

## 5. Kabul kriterleri

1. `FIELD_SETS["safety_event"]` §3.3'teki alanları ve eşanlamlıları içerir; `suggest_mapping` örnek dosyanın başlıklarını doğru eşler; "Yaralanan Adı Soyadı" hiçbir alana eşlenmez ve düşürülür.
2. §3.4 sözlüğü: her iğne ve kısa kod beklenen `event_type`'ı verir; `FA` "FATAL"da eşleşmez; birden çok sınıf eşleşmesi hata; eşleşmeyen etiket hata (varsayılan sınıf yok).
3. Örnek dosya (4.1): 4 geçerli, 3 hatalı satır; kural kodları tablodakiyle birebir; yasaklı sütun değeri `import_staging_rows.raw` dahil **hiçbir tabloda** saklanmaz.
4. Kaynak anahtarı `(source_system, event_ref, case_no)` ile yeniden import güncelleme yapar, yeni satır eklemez; sınıf değişimi yönüyle denetim izine yazılır; tam dökümde eksik vaka silinmez.
5. Örnek 4.2: LTIFR **28,57**, TRIR **5,71**, oran dışı vaka **1**, KPI-043 **2,86**; hiçbirinde renk yok.
6. Örnek 4.3: çok vakalı olayda LTIFR payı 1, TRIR payı 2; ağırlaşma ölümlü uyarısını açar; düşürme S6 sayacını artırır.
7. Kapsam beyanı olmayan kapsam × ay için İSG oranları `insufficient_data`; beyan var ve olay yoksa `0`.
8. `third_party` vakaları hiçbir orana girmez, ölümlü uyarısına girer.
9. Hiçbir API yanıtı veya hata raporu kişi adı, kimlik, tanı, yaralanma türü veya serbest metin içermez.

## 6. Önerilen test adları

- `test_safety_import_mapping_suggestion_tr_en_ru`
- `test_safety_import_prohibited_columns_never_stored`
- `test_classification_dictionary_exact`
- `test_short_codes_match_whole_token_only`
- `test_classification_ambiguous_is_error`
- `test_generic_work_accident_derived_lti_only_with_lost_days`
- `test_lost_days_class_conflict_is_error`
- `test_event_date_in_future_rejected`
- `test_reported_before_event_rejected`
- `test_worker_category_mapping_and_required`
- `test_case_level_counting_multi_victim_event`
- `test_reimport_updates_case_and_records_direction`
- `test_missing_case_in_full_extract_not_deleted`
- `test_third_party_excluded_from_rates_included_in_fatality_alert`
- `test_subcontractor_case_without_timesheet_coverage_out_of_rate`
- `test_hse_coverage_declaration_zero_vs_insufficient`
- `test_safety_import_sample_file_expected_results` (4.1 + 4.2)

## 7. Etkilenen sözleşmeler / ADR gerekli mi

**ADR gerekli — evet** (yeni import tipi + model alanları + oran payının vaka düzeyine ve kapsama bağlanması).

| Sözleşme | Değişiklik | Sahip |
|---|---|---|
| `SafetyEvent` şeması | §3.3 yeni sütunlar; birim kayıt = vaka | A05 (yeni Alembic revizyonu) |
| Import tipi `safety_event` | Alan seti, eşleme, doğrulama, güncelleme anahtarı | A08 |
| `_safety_rate` pay/payda kuralı | `third_party` dışlama; puantaj kapsamı olmayan firma vakalarını orandan çıkarıp ayrıca sayma; kapsam beyanı | A09 |
| Veri kapsamı meta verisi | B-20 ortak tablo — DATA-03 ile tek tasarım | A05/A08/A09 (Agent 19 spesifikasyonu) |
| Yasaklı sütun listesi | `_HEALTH_COLUMN_HINTS` genişlemesi (izin import'unu da etkileyebilir) | A08, A11 |

## 8. Teşvik ve manipülasyon riski (bildirim karşıtı teşvik analizi)

| Risk | Önlem |
|---|---|
| Kaynakta sınıfı düşürme (LTI → RWC/MTC/FAC) | V7 kayıp gün–sınıf çelişkisi hata; V15 aşağı yönlü yeniden sınıflandırma sayacı (S6); RWC payı sinyali (S4) |
| "İş kazası" yazıp ağırlığı boş bırakarak oranlardan kaçma | Genel etiket ancak kayıp günle LTI'ya türetilir; aksi halde **hata** — satır kaybolmaz, kuyrukta bekler ve sayılır |
| Kötü vakaları sonraki dökümden çıkararak silme | V16: otomatik silme yok |
| Alt yüklenici vakalarını "başka firmaya" yazma | Firma = etkilenen çalışanın işvereni (tanım); oran dışı vaka sayısı görünür; ölümlü uyarısı tüm kategorileri sayar |
| Olay tarihini pencere dışına kaydırma | V2/V3 tarih kuralları; olay tarihi değişikliği manuel incelemeye düşer (V15) |
| Geç kaydı gizlemek için iç bildirim tarihini boş bırakma | `reported_date` boş oranı veri kalite puanına yansır (öneri; A09) |
| Ramak kala beslemesini hiç göndermeme | Kapsam beyanında `includes_near_miss = hayır` → KPI-043 `insufficient_data` (0 değil); S2 sessizlik sinyali |
| Kişi verisi kaygısıyla İSG verisi paylaşımından kaçınma | Import kişi/tanı içermez; yasaklı sütunlar düşürülür — paylaşmanın gizlilik maliyeti düşüktür |

## 9. ⚖️ maddeler

- ⚖️ **5510 md. 13** — iş kazası tanımı (işverence sağlanan taşıtla toplu gidiş-geliş dahil) → `commuting_policy_required` kuralı; **teyit edilmeli**.
- ⚖️ **5510 md. 14** — meslek hastalığı; v1'de olay import'unda desteklenmez — **teyit/politika**.
- ⚖️ **6331 md. 14** — kayıt ve bildirim; (1)(b) ramak kala niteliğindeki olaylar — **teyit edilmeli**.
- ⚖️ **4857 md. 7** — geçici iş ilişkisi işçileri (`temporary_agency_worker`) için İSG yükümlülüklerinin dağılımı — **teyit edilmeli**.
- ⚖️ **KVKK md. 4 (veri minimizasyonu) ve md. 6** — kimliksiz vaka satırı bile küçük projede tarih + proje + sınıf ile kişiyi belirlenebilir kılabilir: vaka düzeyi liste görünümü yalnızca İSG yetkili rolüne, diğer rollere yalnızca agregalar (Agent 20 teyidi).
- OSHA 29 CFR 1904.7 (gün sayımı, 180 gün tavanı, ilk yardım listesi), GRI 403-9 (2018), ESRS S1-14, ANSI Z16.1 — tanım kaynağı, yasal değil; madde numaraları **teyit edilmeli**.

## 10. Açık sorular (Agent 17 için)

- **Q1 — Birim kayıt:** vaka düzeyi (öneri) onaylanıyor mu? Mevcut demo/seed "bir satır = bir olay" ile uyumsuz; geçiş için ADR.
- **Q2 — Meslek hastalığı vakaları:** v1'de olay import'unda reddedilsin (öneri) mi, yeni `occupational_illness` türü mü açılsın (TRIR'a girmeden)?
- **Q3 — Servis/işe gidiş-geliş kazaları:** oranlara dahil mi? (SGK iş kazası sayar; GRI yalnızca organize taşıma; OSHA dışlar.) Öneri: sayılsın ama ayrı alt sayımla raporlansın — hukuk + İSG kararı.
- **Q4 — KPI-044 kayıp gün tavanı:** vaka başına 180 gün (OSHA) mı, tavansız mı?
- **Q5 — `worker_category` için alt yüklenici vaka oranları** (GRI 403-9 ayrı açıklama) bir sonraki adımda ayrı gösterge olsun mu?
- **Q6 — `sgk_notification` alanları** alınsın mı? Öneri: evet ama yalnızca toplu "bildirim durumu dağılımı" olarak; platform uyumluluk yargısı üretmez.

## 11. İnsan İSG profesyoneli / hukukçu gerektirenler

- Şirketin gerçek İSG kayıt sistemindeki sınıf etiketlerinin ve LTI tanımının ("kaç gün kayıp") §3.4–3.5 ile eşlenmesinin iş güvenliği uzmanınca onayı (HR Director §6.1).
- `commuting_policy_required`, `out_of_scope_event` ve `illness_not_supported_v1` kuyruklarındaki kayıtların karara bağlanması — iş güvenliği uzmanı / işyeri hekimi.
- Alt yüklenici sözleşmelerine İSG vaka ve puantaj verisi bildirme şartı — satın alma / sözleşme yönetimi.

## 12. Statü

**Taslak — İK Direktörü incelemesi bekliyor.** Onaylı değildir. Agent 19'un DATA-03 belgesiyle birleştirilmesi Agent 17 üzerinden yapılır.

---

## İK Direktörü incelemesi (2026-11-05)

**İnceleyen:** Agent 17 (İK Direktörü) · **Karar: ŞARTLI ONAY** · Ölçütler: `HR_TEAM_PLAN.md` §2.4 ve §4.2.

### A. El hesaplarının yeniden hesaplanması

| Örnek | Benim hesabım | Sonuç |
|---|---|---|
| 4.1 satır sonuçları | 1 ✔ LTI; 2 ✔ MTC (05.03.2026 Perşembe → 12.03 Perşembe: 6, 9, 10, 11, 12 Mart = 5 iş günü > 3 → S5 girdisi); 3 ✔ NM; 4 ✖ FAC + 4 gün; 5 ✔ türetilmiş LTI (3 gün); 6 ✖ `severity_required`; 7 ✖ 2026-12-01 > 2026-11-05 → 4 geçerli / 3 hatalı | ✔ eşleşiyor |
| 4.2 LTIFR | 2/70.000 × 10⁶ = 28,57 | ✔ |
| 4.2 TRIR | 2/70.000 × 200.000 = 5,71; bugünkü kod 3/70.000 × 200.000 = 8,57 | ✔ |
| 4.2 KPI-043 | 1/70.000 × 200.000 = 2,857 | ✔ |
| 4.3 | Vaka düzeyinde LTIFR payı 1 (LTI), TRIR payı 2 (LTI + MTC); FAC hiçbir oranda yok | ✔ |

### B. Onay koşulları

1. **Kapsam beyanı alan adları WP-6 veri belgesiyle birleşir** (terim kararı, `SPRINT1_REVIEW.md` §3): `coverage_from/coverage_to` → `period_start/period_end`; `includes_subcontractor_cases`, `includes_near_miss` → `data_feed_coverage.attributes`; tablo/alan sahibi WP-6 veri §3.4. Import tipi adı `safety_event`, kapsam besleme tipi `safety`.
2. **Türkçe ve Rusça sözcük iğneleri katlamadan sonra sözcük sınırında eşleşir** (kısa kodlar için zaten öyle). Gerekçe: aksan sökme sonrası çakışmalar — ör. "ölümlü" → `olumlu` ("olumlu" = pozitif), "ölüm" → `olum` ("olumsuz" içinde geçer); RU `почти` tek başına "neredeyse" demektir → `почти происш` / `почти-происш` kullanılır. Belirsizlik zaten hata üretir (kural 3) ama yanlış **tekil** eşleşme sessiz kalır.
3. **İşyeri tehlike sınıfı (`hazard_class`)** alanı WP-6 veri §3.3.2'de "Agent 18'in alanı" olarak atıf alıyor ama bu belgede tanımlı değil. Sprint 2'de Agent 18 tanımlar (proje veya işveren düzeyi, etkin tarihli, değerler az/tehlikeli/çok tehlikeli ⚖️ WP-3 OHS-04). O zamana kadar KPI-008 "yasal asgari karşılanmıyor olabilir" notu üretilmez; yalnızca kırılım gösterilir.
4. Yasaklı sütun listesinde `sorumlu` gibi iğneler meşru firma sütunlarını ("Sorumlu firma") düşürebilir; §3.9'daki "eşlenmiş sütun yasak listeye takılırsa hata ve yeniden adlandırma" kuralı **zorunlu** kabul kriteri olur (sessiz düşürme yok), test: `test_prohibited_column_mapped_field_raises_rename_error`.
5. Alt yüklenici vakaları (`legal_entity_code` = etkilenen çalışanın işvereni) bugünkü erişim hatası düzeltilmeden (Orchestrator doğrulaması (b): `access.py` `ContractorRelationship.project_id`'yi yok sayıyor ve paylaşım kademeli yayılıyor) **gerçek veriyle** yüklenmez: aksi halde bir projede verilen paylaşım izni, firmanın tüm projelerdeki vaka satırlarını açar. Uygulama sırası: erişim düzeltmesi önce (`SPRINT1_REVIEW.md` §5).
6. V15/S6 yön kodları WP-1 ile ortak (`reclassified_up` / `reclassified_down`).

### C. Açık sorulara kararlarım

| # | Karar | Gerekçe |
|---|---|---|
| Q1 | **Vaka düzeyi onaylandı** (bir satır = bir etkilenen kişinin sonucu, kimliksiz) | GRI 403-9 / OSHA vaka sayar; olay düzeyi çok yaralılı olaylarda oranı eksik gösterir (4.3). Seed/demo geçişi ADR'de |
| Q2 | **v1'de reddedilir (`illness_not_supported_v1`) ama kaybolmaz:** kuyruktaki bekleyen sayı İSG rolüne her ay gösterilir; meslek hastalığı istirahati izin tarafında `work_injury` ile zaten görünür (WP-1). Ayrı `occupational_illness` türü sprint 2 adayı (GRI 403-10) | TRIR "injury" tanımını bozmamak; vakanın sessizce düşmemesi |
| Q3 | `commuting_policy_required` kuyruğu WP-3 OHS-10(b) yanıtına kadar kalır. Yanıttan sonra iş tercihim: **işverenin sağladığı servis kazaları oranlara dahil**, kişisel ulaşım kazaları oran dışı; ikisi de ayrı alt sayımla raporlanır | 5510 md. 13 ve GRI 403-9 "organize taşıma" ile uyumlu; servis kazası işverenin kontrolündedir |
| Q4 | KPI-044 aktive edilirse **vaka başına 180 gün tavanı oranda**; tavansız toplam ayrıca saklanır ve gösterilir | Uluslararası karşılaştırılabilirlik + ağır vakanın görünmezleşmemesi |
| Q5 | **Evet, sprint 2:** alt yüklenici çalışanları için ayrı oran (GRI 403-9) | Alt yüklenici vakası ana yüklenici oranında kaybolmamalı; önce puantaj kapsamı oturmalı |
| Q6 | **Evet, alınır — yalnızca toplu dağılım** ("bildirildi / beklemede / gerekmiyor" sayıları); platform uyum yargısı üretmez | OHS-03(a) güvenli varsayılanı ile tutarlı |

### D. Statü

**İK Direktörü onaylı (şartlı)** — koşullar 1–6. Bağımlılıklar: WP-6 veri kapsam meta verisi (önce), erişim düzeltmesi (önce). ADR gerekli (SafetyEvent vaka düzeyi şeması + import tipi). İnsan onayı: iş güvenliği uzmanı — gerçek kaynak sınıf etiketlerinin §3.4 eşlemesi ve LTI gün tanımı (WP-3 OHS-10), KVKK — vaka düzeyi liste görünümü (KV-04).
