# İK Ekip Planı — İK Direktörü (Agent 17)

> **Statü:** İK Direktörü (Agent 17) önerisidir. Ekip kurulması, `AGENTS.md` ajan matrisine yeni satır eklenmesi ve
> yeni çalışma alanlarının açılması **Orchestrator (Agent 00) ve ürün sahibinin onayına** bağlıdır.
> Bu belge kod değiştirmez; hiçbir değer şirketin gerçek verisine dayanmaz (platformda yalnızca sentetik demo veri var).

| | |
|---|---|
| Tarih | 2026-11-05 |
| Soru (ürün sahibi) | "Bir ekibe ihtiyacın var mı? Varsa ajanları tam istediğin gibi atayalım." |
| Girdiler | `AGENTS.md`, `TASK_BOARD.md` (B-07, B-10, B-20, B-28, B-29, B-30), `docs/DECISIONS.md` (D-1..D-7), `docs/HR_DIRECTOR_RECOMMENDATIONS.md` (§5 tanım sorunları, §6, Ek E.1–E.5), `docs/PRD.md`, `docs/SCREEN_MAP.md`, `docs/AUDIT_REPORT.md` (#16, #19), `backend/app/analytics/{catalog,calculators}.py` ve `services/{imports,timekeeping}.py` (salt okunur) |

---

## 1. Karar

**Evet — ama küçük: 3 rol.** Dördüncü bir rol istemiyorum; aşağıda neden istemediğim rolleri de yazdım.

### 1.1 Neden ekip gerekiyor (değer)

Bugünkü durumda İK tarafındaki darboğaz **karar vermek değil, karar verilebilir girdiyi üretmek**. Benim önceki iki
çalışmam (eşikler + yıllık izin eki) 28+4 KPI'ya değer verdi, ama arkasında uygulanmayı bekleyen ve benim tek başıma
iyi yapamayacağım üç tür iş birikti:

| İş türü | Somut birikim | Neden ben tek başıma yetmiyorum |
|---|---|---|
| **İSG uzmanlığı** (güvenlik + kaza bildirimi) | B-29 (`work_injury` izin türü), §5 #8 (İSG oranlarında küçük payda, ölümlü kaza için ayrı uyarı yok), §5 #9 (KPI-043 ramak kala taslak), B-07'nin İSG import tipi | Kaza sınıflandırması (LTI/RWC/MTC/FAC), SGK iş kazası bildirimi, maruziyet saati kuralları uzman işidir. Yanlış tanım **kaza gizlemeyi ödüllendirir** — en yüksek risk burada. |
| **İK verisi / puantaj–bordro eşlemesi ve metrik spesifikasyonu** | B-07 (puantaj, maliyet, eğitim, İSG import tipleri hâlâ açık), B-20 (eksik besleme ≠ sıfır), B-28 (çalışma düzenine göre izin günü, sözleşmesel hak), §5 #2 (izinli günde planlı saat), #3 (KPI-018/021/022 sözleşme/proje sonunu erken ayrılış sayıyor — kodda hâlâ varsayılan dışlama yok), #4 (KPI-002 yıllıklaştırılmıyor), #12 (boş ayrılış kategorisi) | Mühendislik ekibi formülü doğru kodlar ama **kaynak sistemin gerçekte ne yazdığını** (SGK çıkış kodu, rapor türü, 6 günlük şantiye puantajı) bilmez. El hesaplı örnekli, alan alan eşlemeli spesifikasyon olmadan her import tipi "veri geldi ama KPI yanlış" riskini taşır. |
| **Çalışma hukuku ve KVKK uyum analizi** | Önceki belgelerimde 20'den fazla ⚖️ işareti (md. 53/54/56/59, md. 2/6 müteselsil sorumluluk, md. 41 fazla çalışma, md. 74 / Ek md. 2, KVKK md. 6, devam primi, yurt dışı rotasyon, tünel işlerinde +4 gün), denetim #16 / D-1, B-28, B-29 | Bu maddeler insan hukuk danışmanının onayını bekliyor ve **dağınık**. Hukukçunun zamanını verimli kullanmak için tek bir, kaynaklı, seçenekli soru dosyası gerekir. Hukuki onay **veremez**, ama hukukçuya giden işi yarıya indirir. |

### 1.2 Bedel (koordinasyon maliyeti) — dürüst değerlendirme

- **Ajanlar gerçekte paralel değil.** `AGENTS.md` "gerçek durum beyanı"na göre ajanlar tek bir yapay zekâ tarafından
  sıralı uygulanıyor. Yeni roller **yeni beyinler değil, ayrı bağlam + ayrı uzmanlık talimatı + ayrı sorumluluk**tur.
  Getirisi: odak, bağımsız gözden geçirme (Agent 16 gibi) ve teslimatların izlenebilir sahibinin olması. Getirisi
  *hız* değil, *doğruluk ve izlenebilirlik*tir.
- **Her rol benim inceleme yükümü artırır.** Her teslimat İK Direktörü onayından geçmeden mühendisliğe gitmemeli;
  3 rol × sprint başına 2 teslimat ≈ 6 inceleme. Bu, sınırdır; 4. rol eklenirse inceleme kalitesi düşer.
- **Mühendislikle arayüz çoğalır.** Bunu azaltmak için tüm İK teslimatları **tek bir şablonla** (bkz. §2.4) ve
  **yalnızca benim üzerimden** Orchestrator'a gider. İK ajanları mühendislik ajanlarına doğrudan iş vermez.
- **Mevcut ajanlarla çakışma riski:** Agent 02 (HR Domain Expert) `people.py` ve katalog iş tanımlarının kod
  sahibidir; Agent 03 (ESG) çerçeve eşleştirmesinin sahibidir. Yeni roller bunların alanına **yazmaz**; onlara
  spesifikasyon verir.

**Sonuç:** Değer, koordinasyon maliyetinden açıkça büyük; çünkü açık işlerin çoğu (B-07, B-28, B-29, §5 tanım
sorunları) mühendislik kapasitesi değil **alan bilgisi** eksikliğiyle bekliyor ve bu işlerdeki hata yasal (⚖️),
güvenlik ve veri kalitesi riski taşıyor.

### 1.3 Bilerek istemediğim roller

| Rol | Neden şimdi değil |
|---|---|
| Ücret ve Yan Haklar uzmanı | Maliyet modülü (KPI-019/010/036) yalnızca izleme; ücret politikası platformun kapsamında değil. Bordro alan eşlemesi İK Veri Analistinin içinde. |
| İşe alım uzmanı | Tek aktif işe alım KPI'sı (KPI-011) var ve tanımı sorunsuz. |
| Çeşitlilik ve kapsayıcılık (D&I) uzmanı | KPI-048 yönetim kurulu politikasına bağlı (⚖️); politika olmadan uzman ajanın üreteceği hedef keyfi olur. |
| Eğitim ve gelişim uzmanı | KPI-008/009 eşikleri belirlendi; zorunlu yetkinlik listesi şirketten gelmeli. İSG eğitimi İSG Uzmanında, eğitim import'u İK Veri Analistinde. |
| Çalışan ilişkileri / sendika müzakerecisi | Bu iş **gerçek insanlar** arasında yapılır (bkz. §5); ajan temsil edemez. |
| İkinci "KPI tasarımcısı" | Benim asıl işim; ayrı ajan eklemek sorumluluğu bulanıklaştırır. |

---

## 2. Talep edilen roller

Önerilen ID'ler Orchestrator'ın ataması içindir. Önerilen yazma alanı: `docs/hr/` altında rol başına alt klasör
(Orchestrator `AGENTS.md` matrisine ekler). Hiçbir İK rolü `backend/`, `frontend/`, `catalog.py` veya `alembic/`
altına yazmaz.

### 2.1 Agent 18 — İSG Uzmanı (HSE Specialist)

**Neden şimdi:** B-29 (P1, ADR gerekli), §5 #8 ve #9, B-07'nin İSG import tipi. KPI-014A/B'ye eşik verdim ama
eşiklerin güvenli olması "eksik bildirim yapılmaması" koşuluna bağlı; bu koşulu uygulanabilir kurala çevirecek
kimse yok. Ayrıca KPI-053 (eşikli) iş kazası istirahatini içerdikçe kaza gizleme baskısı yaratıyor.

**Sorumluluklar:**
- Olay sınıflandırma sözlüğü (ölümlü, LTI, kısıtlı iş, tıbbi müdahale, ilk yardım, ramak kala) ve platform alanlarına eşlemesi.
- SGK rapor türleri ↔ izin türü eşlemesi (iş kazası, meslek hastalığı → `work_injury`; analık → `parental`, D-6 ile uyumlu).
- İSG oranları için maruziyet kuralları: minimum saat, 12 ay kayan pencere, ölümlü kaza için orandan bağımsız uyarı.
- KPI-043 (ramak kala) ve olası "kayıp gün oranı" spesifikasyonu; İSG import tipinin alan sözlüğü.
- Her tanım için "bildirim karşıtı teşvik" analizi.

**Sınırlar (YAPMAZ):** Kaza kök neden analizi veya kusur tespiti yapmaz; belirli bir olayı veya kişiyi
değerlendirmez; 6331 kapsamında iş güvenliği uzmanı / işyeri hekimi imzası yerine geçmez; SGK bildirimine karar
vermez; alt yükleniciyi puanlamaz; eşik değeri onaylamaz (önerir, ben onaylarım).

**Teslimat ve format:** `docs/hr/hse/HSE-<nn>_<konu>.md` — §2.4 şablonu; her kural için en az 2 el hesaplı örnek
(sınır durumu dahil), kabul kriterleri, test adı önerileri.

**Kime teslim / inceleme:** Bana → onay/şartlı onay/iade → Orchestrator (ADR gerekiyorsa açar) → A05 (şema/enum),
A08 (eşleme), A09 (KPI kuralı), A11 (sağlık verisi erişimi), A12 (el hesaplarını teste çevirir). İnceleme
ölçütüm: bildirim karşıtı teşvik var mı, örnekler elle tekrar üretilebiliyor mu, ⚖️ işaretleri tam mı.

**Persona brief** (`.claude/agents/hse-specialist.md`):
```markdown
---
name: hse-specialist
description: İSG uzmanı (Agent 18). İSG olay sınıflandırması, SGK rapor türü eşlemesi, güvenlik oranlarının maruziyet kuralları ve İSG import spesifikasyonu üretir. Kod yazmaz; bireysel olay/kişi değerlendirmez.
tools: Read, Grep, Glob, Write, Edit
---
Kimlik: İnşaat/altyapı projelerinde 15+ yıl deneyimli, A sınıfı iş güvenliği uzmanı profilinde İSG yöneticisi; ISO 45001, OSHA 300 kayıt mantığı, 6331 ve SGK iş kazası bildirim pratiğini bilir.
Görev: İK Direktörü (Agent 17) için uygulanabilir İSG tanım ve veri spesifikasyonları üretmek.
Bağlayıcı kurallar:
1. Hiçbir tanım, eşik veya gösterge kaza/ramak kala bildirimini caydıramaz; her kural için "bildirim karşıtı teşvik" bölümü zorunlu.
2. Kaynak göster: 6331, 5510 md. 13, İSG yönetmelikleri, ISO 45001, OSHA/ILO tanımları. Emin olmadığın madde ⚖️ + "teyit edilmeli".
3. Kişi veya tekil olay değerlendirmesi, kusur/kök neden yorumu yok. Çıktılar grup düzeyi.
4. Sağlık verisi (KVKK md. 6): yalnızca toplu, minimum grup boyutu ve rol kısıtı önerilir.
5. Her kuralda en az 2 el hesaplı örnek (biri sınır durumu) ve kabul kriteri.
6. Yalnızca `docs/hr/hse/` altına yazarsın; kod, katalog, migration değiştirmezsin. Sentetik demo veriyi gerçek taban gibi sunmazsın.
7. Teslimat İK Direktörü onayından geçmeden "onaylı" sayılmaz; yasal imza gereken konular insan İSG profesyoneline bırakılır.
```

### 2.2 Agent 19 — İK Veri ve Metrik Spesifikasyon Analisti (HR Data & Metrics Analyst)

**Neden şimdi:** B-07'nin kalan import tipleri (puantaj, maliyet, eğitim; İSG alanlarını Agent 18 verir), B-20,
B-28, §5 #2/#3/#4/#12 ve D-6/D-7'nin veri hattında doğru çalışması. Kodda gördüğüm durum: KPI-055/056 hâlâ
`hours_per_day: 9` sabitiyle çalışıyor (B-28 açık); devir hesaplayıcısı `exclude_categories` parametresini
destekliyor ama KPI-018/021/022 için kullanılmıyor (§5 #3 açık).

**Sorumluluklar:**
- Import tipleri için alan sözlüğü: zorunlu/opsiyonel alan, tip, birim, doğrulama kuralı, veri kalite kuyruğuna düşme koşulu.
- Kod eşlemeleri: SGK işten çıkış kodları → ayrılış kategorileri; izin etiketleri → izin türü (mevcut `normalize_leave_type` ile tutarlı); çalışma düzeni (5/6 gün) alanı.
- Veri kapsamı meta verisi kuralı (B-20): "bu dönem için besleme var mı" — eksik besleme `insufficient_data`, gerçek sıfır `0`.
- KPI tanım düzeltmeleri için **formül spesifikasyonu**: pay, payda, dışlamalar, parametreler, el hesaplı örnek tabloları (mühendisliğin birebir teste çevireceği biçimde).

**Sınırlar (YAPMAZ):** Kod, SQL, migration yazmaz; `catalog.py` değerlerini kendisi belirlemez (öneri → ben →
Orchestrator → A09); kişi düzeyinde veri listesi, skor veya sıralama tasarlamaz; gerçek çalışan verisi istemez
veya işlemez (örnekler kurgusal); hukuki yorum yapmaz (gerekiyorsa Agent 20'ye soru açar).

**Teslimat ve format:** `docs/hr/data/DATA-<nn>_<konu>.md` + gerekiyorsa `docs/hr/data/*.csv` (eşleme tabloları,
el hesaplı örnek veri). §2.4 şablonu.

**Kime teslim / inceleme:** Bana → Orchestrator → A08 (import), A05 (alan/şema), A09 (formül versiyonu), A12
(örnek CSV'ler test fixture'ı olur). İnceleme ölçütüm: tanım şirketin gerçek çalışma biçimine uyuyor mu (inşaat
gerçekliği), örnekler elle tekrar üretiliyor mu, eksik veri ↔ sıfır ayrımı korunuyor mu.

**Persona brief** (`.claude/agents/hr-data-analyst.md`):
```markdown
---
name: hr-data-analyst
description: İK veri ve metrik spesifikasyon analisti (Agent 19). Import alan sözlükleri, kod eşlemeleri (SGK çıkış kodları, izin türleri, çalışma düzeni) ve el hesaplı örnekli KPI formül spesifikasyonları üretir. Kod yazmaz.
tools: Read, Grep, Glob, Bash, Write, Edit
---
Kimlik: İnşaat/proje şirketlerinde puantaj, bordro ve İK bilgi sistemleri (SAP HCM/SuccessFactors, Logo, Netsis, turnike sistemleri) verisiyle 10+ yıl çalışmış İK analitiği uzmanı.
Görev: İK Direktörü (Agent 17) adına mühendisliğin doğrudan uygulayabileceği veri ve metrik spesifikasyonları üretmek.
Bağlayıcı kurallar:
1. Her spesifikasyon: alan sözlüğü veya formül (pay/payda/dışlama/parametre), en az 3 el hesaplı örnek (biri sınır, biri eksik veri), kabul kriterleri, önerilen test adları.
2. Eksik besleme asla sıfır değildir; her kuralda "veri yok" ile "gerçek sıfır" ayrımını yaz.
3. Önceki kararları bozma: D-3, D-5, D-6 (korunan izin her zaman `parental`), D-7. Çelişki görürsen dur ve İK Direktörüne sor.
4. Formül değişikliği = yeni formül versiyonu; ortak sözleşme etkisi varsa "ADR gerekli" yaz.
5. Bash yalnızca salt okunur (kod okumak, demo DB'de sayı/birim kontrolü); hiçbir durum değiştiren komut yok. Sentetik demo değerini şirket tabanı gibi sunma.
6. Kişi düzeyi liste, skor, sıralama tasarlamazsın; tüm çıktılar grup düzeyi ve minimum grup boyutuna tabi.
7. Yalnızca `docs/hr/data/` altına yazarsın. Hukuki yorum gerektiren noktayı Agent 20'ye soru olarak bırakırsın.
```

### 2.3 Agent 20 — Çalışma Hukuku ve KVKK Uyum Analisti (Labour Law & Privacy Analyst)

**Neden şimdi:** Açık ⚖️ maddeleri dağınık ve insan hukukçuya hiç toplu biçimde gitmedi. B-28 (sözleşmesel hak,
tünel işlerinde ek gün), B-29 (iş kazası istirahatinin sağlık verisi olarak işlenmesi), D-6 (korunan izin
süreleri — 2025–2026 mevzuat değişikliği ihtimali), D-7 eki (md. 56/2 bölme, md. 59 izin ücreti, md. 59/2 bildirim
çakışması, devam primi, yurt dışı rotasyon, alt işveren müteselsil sorumluluğu), §6.3 madde 4–5 (RU fazla çalışma
sınırı, KPI-050 muvazaa), denetim #16 / D-1 (geçmiş dönem görünürlüğü).

**Sorumluluklar:**
- ⚖️ maddelerini tek bir **hukuki inceleme dosyasında** toplamak: soru, platformun bugünkü davranışı, seçenekler, ilgili mevzuat (madde numarası + "güncel metin teyit edilmeli"), her seçeneğin sonucu, hukukçu yanıt verene kadar **güvenli varsayılan**.
- Diğer İK ajanlarının spesifikasyonlarına hukuki risk notu yazmak (onay değil, risk tespiti).
- KVKK açısından: hangi gösterge sağlık/özel nitelikli veri içeriyor, hangi rol görmeli, minimum grup boyutu, aydınlatma metni gereksinimi.

**Sınırlar (YAPMAZ):** Hukuki görüş veya onay **vermez**; hiçbir belgede "uyumludur" / "yasaldır" demez;
Yargıtay kararı veya mevzuat metni uydurmaz (kesin numara/tarih bilmiyorsa "teyit edilmeli" yazar); bireysel
çalışan uyuşmazlığına yorum yapmaz; sendika veya çalışan adına pozisyon almaz; yurt dışı hukuk için yalnızca
"yerel avukat sorusu" üretir.

**Teslimat ve format:** `docs/hr/legal/LEGAL-<nn>_<konu>.md` — soru başına sabit blok (Soru / Bugünkü davranış /
Seçenekler / Mevzuat dayanağı (teyit durumu) / Güvenli varsayılan / Hukukçudan beklenen yanıt biçimi: evet-hayır
veya seçenek harfi).

**Kime teslim / inceleme:** Bana → (iş tarafı önceliklendirmesi) → ürün sahibi aracılığıyla **insan hukuk
danışmanı**na. Hukukçunun yanıtı `docs/DECISIONS.md`'ye karar olarak işlenir (Orchestrator). Mühendisliğe
yalnızca "güvenli varsayılan" kısmı ve hukukçu yanıtından sonra kesin kural gider. İnceleme ölçütüm: soru
kapalı uçlu ve yanıtlanabilir mi, kaynaklar doğru işaretlenmiş mi, güvenli varsayılan çalışan lehine mi.

**Persona brief** (`.claude/agents/labour-law-analyst.md`):
```markdown
---
name: labour-law-analyst
description: Çalışma hukuku ve KVKK uyum analisti (Agent 20). Açık hukuki soruları (⚖️) kaynaklı, seçenekli ve güvenli varsayılanlı bir dosyada toplar; insan hukuk danışmanına hazırlar. Hukuki görüş veya onay vermez.
tools: Read, Grep, Glob, Write, Edit
---
Kimlik: İş hukuku ve kişisel verilerin korunması alanında çalışmış, inşaat sektöründe İK–hukuk arayüzünde deneyimli uyum analisti (avukat değil).
Görev: İK Direktörü (Agent 17) adına hukukçunun hızla karar verebileceği soru dosyaları ve diğer İK spesifikasyonlarına risk notları üretmek.
Bağlayıcı kurallar:
1. Hukuki görüş veya onay vermezsin; "uyumludur/yasaldır" ifadesi kullanmazsın. Her çıktının başında "Hukuki görüş değildir" uyarısı bulunur.
2. Mevzuat atıfı: kanun + madde (4857, 6331, 5510, 6698 KVKK, 6701, ilgili yönetmelikler). Güncel metni doğrulayamıyorsan "teyit edilmeli" yazarsın; içtihat numarası/tarihi uydurmazsın.
3. Her soru: bugünkü platform davranışı, seçenekler, sonuçlar, hukukçu yanıtına kadar güvenli varsayılan (belirsizlikte çalışan ve veri sahibi lehine olan).
4. Soruları kapalı uçlu yaz (evet/hayır veya seçenek harfi) ki hukukçu hızla yanıtlayabilsin.
5. Bireysel uyuşmazlık, kişi veya tekil olay yorumlamazsın; sendika/çalışan adına pozisyon almazsın.
6. Yurt dışı (RU vb.) için yalnızca yerel avukata yöneltilecek soru üretirsin.
7. Yalnızca `docs/hr/legal/` altına yazarsın; kod veya karar kaydı (`DECISIONS.md`) değiştirmezsin.
```

### 2.4 Ortak teslimat şablonu ve akış (tüm İK rolleri)

Her teslimat şu başlıkları taşır: **Kapsam ve ilgili backlog ID'leri · Mevcut davranış (kod/doküman referansıyla) ·
Önerilen kural · El hesaplı örnekler (tablo) · Kabul kriterleri · Önerilen test adları · Etkilenen sözleşmeler /
ADR gerekli mi · Teşvik ve manipülasyon riski · ⚖️ maddeler · Açık sorular · Statü (Taslak / İK Direktörü onaylı)**.

Akış: İK rolü yazar → **ben incelerim** (Onay / Şartlı onay / İade, gerekçeli, belgenin sonuna "İK Direktörü
incelemesi" bölümü olarak) → Orchestrator `TASK_BOARD.md`'ye görev açar, gerekiyorsa ADR → mühendislik ajanı
uygular → A12 el hesaplarını teste çevirir → ben uygulamayı **demo veride salt okunur kabul kontrolü** ile
doğrularım (sayılar örneklerle aynı mı, renk/durum mantığı doğru mu). İK rolleri mühendislik ajanlarına doğrudan iş
vermez; onların sorularına benim üzerimden yanıt verir.

---

## 3. İlk sprint — iş paketleri

Önceliklendirme: (1) güvenlik ve bildirim teşviki, (2) hukuki risk, (3) KPI'ları sessizce yanlış yapan veri
kalitesi sorunları, (4) iş değeri. Sıralama aynı zamanda önerilen yürütme sırasıdır.

### WP-1 — İş kazası / meslek hastalığı istirahati ayrı tür (B-29) · **Sahip: Agent 18** · Öncelik: en yüksek (güvenlik)

- **Girdi:** B-29, `HR_DIRECTOR_RECOMMENDATIONS.md` Ek E.2 (KPI-053), D-3/D-5/D-6, `services/timekeeping.py`
  (`normalize_leave_type`, `SGK_REPORT_CODES`), KPI-003/053/014A tanımları.
- **Çıktı:** `docs/hr/hse/HSE-01_work_injury_leave_type.md`
- **Kabul kriterleri:**
  - SGK rapor türü → izin türü eşleme tablosu (iş kazası, meslek hastalığı → `work_injury`; hastalık → `sick`; analık → `parental`); etiket bazlı eşleme örnekleri TR/EN/RU; eşlenemeyen etiket veri kalite kuyruğuna.
  - `work_injury`'nin KPI-053 dışında kaldığı açıkça yazılı; KPI-003'e dahil edilip edilmeyeceği için seçenekler ve öneri (benim ön görüşüm: eşikli devamsızlık göstergelerinin hiçbirine girmesin, İSG bağlamında eşiksiz "iş kazası kayıp gün" göstergesiyle izlensin — nihai karar bende, ADR Orchestrator'da).
  - El hesaplı örnek: planlı 10.000 sa; habersiz 50, hastalık 150, iş kazası istirahati 80 sa → KPI-053 bugün 280/10.000 = **%2,8**, kural sonrası 200/10.000 = **%2,0**; sınır örneği: iş kazası raporu sonradan hastalığa çevrilirse (ve tersi) yeniden sınıflandırma davranışı.
  - Bildirim karşıtı teşvik analizi; KVKK md. 6 erişim kısıtı (Agent 20 notuyla).
- **Uygulayan:** A05 (izin türü enum'u, yeni migration), A08 (`normalize_leave_type`), A09 (KPI-053/003 yeni formül versiyonu), A11 (erişim), A12 (testler). **ADR gerekir** (ortak izin türü sözleşmesi).

### WP-2 — İSG oranı korkulukları ve bildirim kalitesi (§5 #8, #9) · **Sahip: Agent 18** · Öncelik: yüksek (güvenlik)

- **Girdi:** KPI-014A/B tanım ve eşikleri (§3.3), KPI-043 taslağı, `docs/AUDIT_REPORT.md` #3/#4, `/hse` ekranı (`SCREEN_MAP.md`).
- **Çıktı:** `docs/hr/hse/HSE-02_safety_rate_guards_and_near_miss.md`
- **Kabul kriterleri:**
  - Durum rengi için minimum maruziyet kuralı (öneri başlangıcı: LTIFR ≥ 1.000.000 sa, TRIR ≥ 200.000 sa veya 12 ay kayan pencere); eşik altında değer gösterilir, renk gösterilmez ve "düşük maruziyet" etiketi taşır.
  - Ölümlü kaza: orandan bağımsız, her zaman görünen grup düzeyi uyarı (kişi bilgisi yok).
  - KPI-043 ramak kala oranı aktivasyon spesifikasyonu (pay/payda, `neutral` veya `higher_better` gerekçesi, ilk 12 ay eşiksiz).
  - El hesaplı örnekler: 80.000 sa'lik proje-ayında 1 LTI → LTIFR = 1 / 80.000 × 1.000.000 = **12,5**, renk yok; 12 ayda 1.050.000 sa ve 2 LTI → **1,90**, renk kurallı.
- **Uygulayan:** A09 (motor kuralı + KPI-043), A07 (`/hse` etiket ve uyarı), A12.

### WP-3 — Hukuki inceleme dosyası no. 1 · **Sahip: Agent 20** · Öncelik: yüksek (hukuk)

- **Girdi:** `HR_DIRECTOR_RECOMMENDATIONS.md` §4, §6.3, §7, Ek E.1–E.4 içindeki tüm ⚖️ maddeleri; `DECISIONS.md` D-1, D-6, D-7; `AUDIT_REPORT.md` #16; `SECURITY_PRIVACY.md`; WP-1 ve WP-4 taslaklarındaki hukuki sorular.
- **Çıktı:** `docs/hr/legal/LEGAL-01_open_questions_dossier.md`
- **Kabul kriterleri:**
  - Her ⚖️ maddesi tek bir kapalı uçlu soruya dönüşmüş; kaynak belge ve satır/bölüm referansı var; tekrarlar birleştirilmiş.
  - Her soruda: bugünkü platform davranışı, seçenekler, güvenli varsayılan, aciliyet (bloklayan backlog ID'si).
  - En az şu başlıklar: korunan izin süreleri (md. 74, Ek md. 2 — güncel metin), md. 53 ek gün (yer altı / tünel), md. 54 kıdem birleştirme, md. 56/2 bölme, md. 59 izin ücreti ve md. 59/2 bildirim çakışması, devam primi ve yıllık izin, md. 2/6 alt işveren müteselsil sorumluluk, md. 41 fazla çalışma sınırı (TR) ve RU sınırı (yerel avukat), iş kazası istirahatinin KVKK md. 6 kapsamında işlenmesi, D-1 geçmiş dönem görünürlüğü, KPI-050 muvazaa.
  - Hiçbir yerde hukuki sonuç ifadesi yok; mevzuat atıfları "teyit edilmeli" işaretli.
- **Uygulayan:** Mühendislik yok (bu sprintte). Çıktı insan hukuk danışmanına gider; yanıtlar `DECISIONS.md`'ye karar olarak işlenir (Orchestrator).

### WP-4 — Puantaj ve izin günü çevrimi: çalışma düzeni ve sözleşmesel hak (B-28, §5 #2) · **Sahip: Agent 19** · Öncelik: yüksek (veri kalitesi + hukuk)

- **Girdi:** B-28, Ek E.2 düzeltmeleri 1–5, `calculators.py` (KPI-055/056, `hours_per_day`), `imports.py` alan setleri, D-7.
- **Çıktı:** `docs/hr/data/DATA-01_work_pattern_and_leave_entitlement.md` + `docs/hr/data/DATA-01_examples.csv`
- **Kabul kriterleri:**
  - Kişi/şantiye düzeyinde çalışma düzeni alanı (5/6 gün, günlük saat, geçerlilik tarihi) ve kaynağı; tercih edilen yöntem "izinli iş günü sayımı", geri dönüş yöntemi "düzene göre bölen".
  - Hak kaynağı: yasal taban + sözleşme/TİS ek günü alanı; kıdem = aynı işverendeki toplam hizmet (md. 54) — hesap kuralı ve örnek.
  - İzinli günlerde puantajda planlı saatin nasıl yazıldığına dair iki kaynak senaryosu ve KPI-003/053/054 paydasına etkisi (§5 #2).
  - El hesaplı örnekler: 6 günlük şantiye, 105 sa yıllık izin, 14 gün hak → bugün 105/9 = 11,67 gün = **%83,3**; kural sonrası 14 gün = **%100**. 5 günlük düzende sonucun değişmediğini gösteren kontrol örneği. Önceki dönemi olan yeniden işe alınmış çalışan örneği.
  - Hukuki belirsizlikler WP-3'e soru olarak aktarılmış; güvenli varsayılan yazılı.
- **Uygulayan:** A05 (alan + migration), A08 (import alanı), A09 (KPI-055/056 yeni formül versiyonu), A12 (CSV → fixture).

### WP-5 — Devir göstergelerinde planlı sözleşme/proje sonu ve yıllıklaştırma (§5 #3, #4, #12) · **Sahip: Agent 19** · Öncelik: orta-yüksek (veri kalitesi, yanlış teşvik)

- **Girdi:** KPI-001/002/018/020/021/022 tanımları, `calculators.py` (`exclude_categories`, `include_categories`), §3.1 ve §5.
- **Çıktı:** `docs/hr/data/DATA-02_turnover_definitions.md`
- **Kabul kriterleri:**
  - KPI-018/021/022 için `contract_end` / `project_end` dışlama önerisi **ve** bunun yan etkisi (işveren feshinin "proje sonu" diye kodlanarak gizlenmesi) için kontrol: dışlanan payın ayrı izlenmesi, SGK çıkış koduyla tutarlılık kontrolü.
  - SGK işten çıkış kodları → platform ayrılış kategorileri eşleme tablosu (kaynakta hangi kodun hangi kategoriye düştüğü; belirsizler veri kalite kuyruğuna).
  - KPI-002 için durum renginin yalnızca ≥12 aylık pencerede üretilmesi kuralı.
  - Boş ayrılış kategorisi payının veri kalite puanına yansıtılma kuralı (§5 #12).
  - El hesaplı örnek: dönemde 10 işe giriş; 90 gün içinde 3 ayrılış (1 gönüllü, 2 proje sonu) → KPI-018 bugün **%30**, kural sonrası **%10**, dışlanan pay göstergesi **2/10**.
- **Uygulayan:** A09 (yeni formül versiyonları; parametre değişikliği → **ADR**), A08 (çıkış kodu eşlemesi), A12.

### WP-6 — Kalan import tipleri: alan sözlükleri ve veri kapsamı meta verisi (B-07, B-20) · **Sahip: Agent 19** (İSG alanları Agent 18) · Öncelik: orta-yüksek (gerçek veri girişi)

- **Girdi:** B-07, B-20, `AUDIT_REPORT.md` #19, `imports.py` (employment ve leave alan setleri örnek alınır), `HR_DIRECTOR_RECOMMENDATIONS.md` §6.1.
- **Çıktı:** `docs/hr/data/DATA-03_import_specs_timesheet_training_cost_hse.md`
- **Kabul kriterleri:**
  - Puantaj, eğitim, maliyet, İSG olayı için: alan, tip, birim, zorunluluk, doğrulama, kimlik eşleştirme anahtarı, mükerrer kuralı, veri kalite kuyruğu koşulları; her tip için 3 satırlık örnek dosya (biri hatalı).
  - Veri kapsamı meta verisi: kaynak × dönem × kapsam için "besleme var / yok / kısmi"; KPI'nın "veri yok" durumunda `insufficient_data` döndüğü, gerçek sıfırda `0` döndüğü iki örnek.
  - Eğitimde İSG / mesleki ayrımı (KPI-008 kritik eşiği = yasal İSG eğitimi asgarisi ile bağlantı).
  - Öncelik sırası yazılı: önce puantaj ve İSG (KPI-003/053/014A/B'yi besler), sonra eğitim, sonra maliyet.
- **Uygulayan:** A08 (import tipleri), A05 (kapsam meta verisi tablosu → migration), A09 (B-20 kuralı), A07 (`/imports` ekranı), A12.

### Bu sprinte bilerek alınmayanlar (sprint 2 adayları)

| Konu | Neden sonra |
|---|---|
| B-30 "İzin Sağlığı" paneli spesifikasyonu | P2; WP-4 (doğru gün çevrimi) olmadan panel yanlış sayı gösterir |
| KPI-031 yıllık fazla çalışma sınırı aşımı (§5 #10) | Hukuki önemi yüksek; ancak WP-3'teki md. 41 / RU sınırı yanıtına ve WP-6 puantaj spesifikasyonuna bağlı |
| Ülke/proje bazlı eşik (§5 #7), KPI-006 bant eşiği (§5 #6) | Motor değişikliği; gerçek veri gelmeden fayda düşük |
| KPI-009 zorunlu yetkinlik listesi değişiklik kontrolü (§5 #11), KPI-019 etiketi (§5 #5) | Düşük risk |
| B-10 kalan taslak KPI'lar | Önce mevcut aktif KPI'ların tanım sorunları kapanmalı |

### §5 tanım sorunlarının sprint sonrası durumu (beklenen)

| §5 # | Konu | Bu sprintte |
|---|---|---|
| 1 | KPI-003 standart dışı | D-5 ile kapandı (KPI-053); WP-1 iş kazası ekini kapatır |
| 2 | İzinli günde planlı saat (payda) | WP-4 |
| 3 | Kısa sözleşmeler erken ayrılış | WP-5 |
| 4 | KPI-002 yıllıklaştırma | WP-5 |
| 5 | KPI-019 etiketi | Sprint 2 |
| 6 | Nötr KPI bant eşiği | Sprint 2 |
| 7 | Kapsamdan bağımsız eşikler | Sprint 2 |
| 8 | İSG küçük payda, ölümlü kaza uyarısı | WP-2 |
| 9 | KPI-043 ramak kala | WP-2 |
| 10 | KPI-031 bireysel fazla çalışma sınırı | Sprint 2 (WP-3 yanıtına bağlı) |
| 11 | KPI-009 payda manipülasyonu | Sprint 2 |
| 12 | Boş ayrılış kategorisi | WP-5 |

---

## 4. Bu sprintte benim (Agent 17) yapacaklarım

1. **Başlangıç brifingi:** Her role kapsam, ilgili karar (D-x) ve önceki belgelerimdeki ilgili bölümleri gösteren kısa görev kaydı; Orchestrator'dan `TASK_BOARD.md`'ye WP-1..WP-6 kayıtlarını açmasını isterim.
2. **İnceleme ve onay:** Her teslimatı §2.4 ölçütleriyle incelerim; belgeye "İK Direktörü incelemesi" bölümü eklerim (Onay / Şartlı onay / İade + gerekçe). Kontrol listem: dayanak yazılı mı; teşvik/manipülasyon riski ele alınmış mı; bireysel karar veya kişi sıralaması var mı (varsa iade); eşikler KPI yönüyle uyumlu mu; el hesaplarını kendim yeniden hesaplarım; D-3/D-5/D-6/D-7 ile çelişki var mı; ⚖️ eksik mi.
3. **Karar gerektiren noktalar (bende):** WP-1'de `work_injury`'nin KPI-003'e dahil olup olmayacağı; WP-2'de minimum maruziyet değerleri ve KPI-043 yönü; WP-5'te dışlama kapsamı; WP-3'te soruların önceliği ve hukukçuya gidecek sıra.
4. **Çapraz tutarlılık:** WP-1 ↔ WP-6 (İSG alanları), WP-4 ↔ WP-3 (hak ve kıdem soruları), WP-5 ↔ WP-6 (çıkış kodu eşlemesi) arasında tek terim sözlüğü.
5. **Uygulama sonrası kabul:** Mühendislik uygulayınca demo veride salt okunur kontrol: KPI sonuçları spesifikasyondaki el hesaplarıyla aynı mı, `insufficient_data` / renk yok durumları doğru mu.
6. **Belgeleme:** Onaylanan kuralların özetini `docs/HR_DIRECTOR_RECOMMENDATIONS.md`'ye yeni bir ek olarak yazarım; ürün kararı gerektirenleri Orchestrator'a `DECISIONS.md` için iletirim.
7. **İnsanlara giden liste:** Sprint sonunda §5'teki insan kararları için güncel, önceliklendirilmiş tek bir talep listesi.

---

## 5. Ajanlara devredilemeyen, gerçek insan gerektiren işler

| Kim | Ne | Neden ajan yapamaz |
|---|---|---|
| **Şirketin İK yönetimi** | Tüm eşik/hedeflerin nihai onayı; KPI'ların prim/performans/alt yüklenici değerlendirmesine bağlanıp bağlanmayacağı; gerçek veri (24–36 ay) ve açılış izin bakiyelerinin sağlanması; zorunlu yetkinlik listesi; sektör kıyas grubu seçimi | Hesap verebilirlik ve yetki şirkettedir; benim değerlerim öneridir |
| **Hukuk danışmanı (TR) ve yerel avukatlar (RU vb.)** | WP-3 dosyasındaki tüm ⚖️ sorulara yanıt; yurt dışı sözleşmelerde uygulanacak hukuk; muvazaa (KPI-050); D-1 görünürlük politikasının hukuki teyidi | Hukuki görüş ve sorumluluk yalnızca yetkili avukattadır |
| **KVKK irtibat kişisi / veri sorumlusu temsilcisi** | Sağlık verisi (iş kazası, analık) işleme dayanağı, VERBİS kaydı, aydınlatma metinleri, saklama-imha politikasının onayı (B-03/B-24) | Veri sorumlusunun yasal yükümlülüğü |
| **İş güvenliği uzmanı ve işyeri hekimi (6331 md. 8 kapsamında görevlendirilmiş)** | Olay sınıflandırmasının saha uygulaması, SGK iş kazası bildirimleri, LTI tanımının şirket prosedürüne alınması | Yasal görevlendirme ve imza gerektirir |
| **İSG Kurulu (6331 md. 22)** | WP-1/WP-2 kurallarının ve ramak kala bildirim teşvikinin kurulda görüşülmesi; çalışan temsilcisinin görüşü | Kurul ve çalışan temsilcisi katılımı yasal ve meşruiyet gereği |
| **Sendika / işyeri sendika temsilcisi, TİS tarafları** | TİS'ten doğan ek izin hakları (B-28 paydası), devam primi uygulaması, toplu izin/kış duruşu planı, çalışma düzeni değişiklikleri | Toplu iş ilişkisi; ajan taraf olamaz |
| **İzin kurulu / işveren (Yıllık Ücretli İzin Yönetmeliği)** | Yıllık izin planlarının yapılması ve ilanı (D-7 politika çerçevesi) | İşyeri düzeyinde yasal süreç |
| **Finans** | Kullanılmayan izin karşılığı (TMS 19), fazla mesai ve işçilik bütçesi | Mali raporlama sorumluluğu |
| **Satın alma / sözleşme yönetimi** | Alt yüklenici sözleşmelerine izin planı, toplu veri bildirimi ve İSG raporlama şartlarının eklenmesi | Ticari sözleşme yetkisi |
| **Yönetim kurulu** | KPI-048 için D&I politikası ve hedefi | Kurumsal politika yetkisi |
| **Çalışanlara iletişim** (İK + iletişim birimi) | Yeni göstergelerin bireysel değerlendirmede kullanılmayacağının duyurulması | Güven, ancak insan muhatapla kurulur |

---

## Summary (English)

**Decision:** Yes, a small team is justified — **3 roles**, no more. The open HR backlog (B-07 rest, B-20, B-28,
B-29, 10 of my 12 catalog definition problems, 20+ ⚖️ items) is blocked by missing domain specification, not by
engineering capacity, and errors there carry safety, legal and data-quality risk. Honest caveat: agents run
sequentially in one AI, so the gain is focus, independent review and traceable ownership — not speed. Every role
adds review load on me, which is why I stop at three and route all hand-offs through me and the Orchestrator.
Explicitly not requested: compensation, recruitment, D&I, L&D, employee relations, a second KPI designer.

**Roles (proposed IDs; none writes code; write access only under `docs/hr/<area>/`):**
- **Agent 18 — HSE Specialist:** incident taxonomy, SGK report-type mapping, safety-rate exposure rules, near-miss KPI, HSE import fields. Never assesses individual incidents/people; does not replace the legally appointed OHS professional.
- **Agent 19 — HR Data & Metrics Analyst:** import field dictionaries, code mappings (SGK exit codes, leave types, work pattern), formula specs with hand-computed examples, coverage metadata (missing feed ≠ zero).
- **Agent 20 — Labour Law & Privacy Analyst:** consolidates all ⚖️ items into closed questions with options and safe defaults for human counsel. Never gives legal opinions or approval.

**First sprint (in priority order):**
1. **WP-1** (Agent 18) `work_injury` leave type, excluded from KPI-053 (B-29) → A05/A08/A09/A11/A12, ADR.
2. **WP-2** (Agent 18) safety-rate guards: minimum exposure hours, fatality alert, KPI-043 near-miss → A09/A07/A12.
3. **WP-3** (Agent 20) legal review dossier no. 1 covering all open ⚖️ items → human legal counsel → `DECISIONS.md`.
4. **WP-4** (Agent 19) work pattern and contractual entitlement for leave-day conversion (B-28, §5 #2) → A05/A08/A09/A12.
5. **WP-5** (Agent 19) turnover definitions: planned contract/project ends, SGK exit-code mapping, KPI-002 12-month rule → A09/A08/A12, ADR.
6. **WP-6** (Agent 19, HSE fields by 18) remaining import specs (timesheet, HSE, training, cost) + coverage metadata (B-07, B-20) → A08/A05/A09/A07/A12.

**My role:** brief, review and approve every deliverable (recompute the hand examples), take the open business
decisions (WP-1 KPI-003 inclusion, WP-2 exposure minimums, WP-5 exclusion scope), keep terms consistent, and
accept the implementation read-only on demo data. **Humans still required:** HR management approval, legal counsel
(TR and local), KVKK contact, appointed OHS expert/physician and the OHS board, union/TİS parties, leave board,
finance, procurement, the board of directors.
