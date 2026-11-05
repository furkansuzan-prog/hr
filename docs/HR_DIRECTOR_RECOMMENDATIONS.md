# KPI Hedef ve Eşik Önerileri — İK Direktörü (Agent 17)

> ## ⚠️ HR Director recommendation — requires human HR management approval before activation
> **İK Direktörü önerisidir — devreye almadan önce şirketin İK yönetiminin (ve ⚖️ işaretli maddelerde hukuk biriminin) onayı gerekir.**
> Bu belge bir yapay zekâ ajanının (Agent 17, İK Direktörü rolü) önerisidir; hiçbir değer şirketin gerçek verisine dayanmamaktadır.
> Platformda şu an **yalnızca sentetik demo veri** vardır (tenant `DEMO`). Demo değerleri yalnızca birim ve ölçek kontrolü için kullanılmıştır; **hiçbir eşik demo değere göre ayarlanmamıştır.**

| | |
|---|---|
| Tarih | 2026-11-05 |
| Kapsam | `docs/KPI_TARGETS_REQUEST.md` formundaki 28 aktif KPI (D-4), açık madde B-26 (doğum/ebeveyn izni) |
| Girdiler | `docs/KPI_TARGETS_REQUEST.md`, `docs/KPI_CATALOG.md`, `backend/app/analytics/catalog.py`, `backend/app/analytics/engine.py` (eşik mantığı), `backend/app/analytics/calculators.py`, `docs/DECISIONS.md` (D-1..D-4), `docs/PRD.md`, dev veritabanında salt okunur ölçek kontrolleri |
| Uygulayan | Onaydan sonra platform sahibi (Orchestrator): katalog eşik alanları + `python -m app.seed.reference --apply`. Formül değişmez → yeni formül versiyonu oluşmaz |

---

## 1. Kullanılan ilkeler

1. **Her değerin dayanağı yazılıdır.** Dayanak türleri: Türk mevzuatı (4857 İş Kanunu, 6331 İSG Kanunu, 5510, ilgili yönetmelikler), raporlama pratiği (ISO 30414, GRI 401/403/404/405, ESRS S1), inşaat/proje sektörü gerçekliği ve platformdaki formül tanımı. Bunların hiçbiri sayısal hedef vermiyorsa değer **yargıdır** ve güven düzeyi buna göre "düşük" yazılmıştır.
2. **Yön ile eşik tutarlılığı.** Motor kuralı (`engine._threshold_status`): `lower_better` → değer ≥ kritik ise kırmızı, ≥ uyarı ise sarı; `higher_better` → değer ≤ kritik ise kırmızı, ≤ uyarı ise sarı. Bu nedenle tüm önerilerde `lower_better: hedef < uyarı < kritik`, `higher_better: hedef > uyarı > kritik` sağlanmıştır. "Hedef" renk hesabına girmez, yalnızca gösterilir. Uyarı değerine **tam eşit** sonuç sarı görünür.
3. **Nötr KPI'ya eşik verilmez.** Motor `neutral` yönlü KPI'lar için durum üretmez; büyüklük göstergeleri (kişi sayısı, işe giriş, kıdem, maliyet düzeyi, oran payları) iyi/kötü değil bağlam bilgisidir. Bunlar "yalnızca izleme" olarak işaretlendi.
4. **Yıllıklaştırma.** `annualize=True` olan KPI-001, KPI-008, KPI-020'de eşikler **yıllıklaştırılmış değere** (değer × 365 / dönem gün sayısı) uygulanır; önerilen sayılar yıllık değerlerdir. Diğer oran KPI'ları (saat/saat, olay/saat) dönem uzunluğundan bağımsızdır. **İstisna:** KPI-002 (devamlılık) ve kohort KPI'ları (018/021/022) dönem uzunluğuna bağlıdır ama yıllıklaştırılmaz — bkz. §5.
5. **İnşaat gerçekliği.** Proje bazlı istihdam, proje sonu/sözleşme sonu ayrılışları, mevsimsellik, alt yüklenici rotasyonu "ofis İK" kıyaslarını geçersiz kılar. Toplam devir (KPI-001) yapısal olarak yüksektir; kontrol edilebilir kısım gönüllü devir (KPI-020) ve erken ayrılıştır.
6. **Teşvik etkisi.** Her hedefin oyunlaştırma / yan etki riski yazılmıştır. **İSG eşikleri bildirim yapmayı asla cezalandırmamalıdır** (bkz. §3.3).
7. **Bireysel karar yok.** Eşikler organizasyon, proje, birim ve yüklenici düzeyindedir. Hiçbir eşik bir kişinin değerlendirmesi, ödüllendirilmesi, disipline edilmesi veya işten çıkarılması için kullanılmamalıdır (devamsızlıkta kişi bazlı "Bradford" türü skorlar dahil).
8. **Gözden geçirme.** Bu değerler **geçici başlangıç değerleridir**. Gerçek 24–36 aylık geçmiş yüklendikten sonra (bkz. §6) yeniden kalibre edilmeli; İSG eşikleri üç ayda bir, diğerleri yılda bir gözden geçirilmelidir.

---

## 2. Ana tablo — doğrudan uygulanabilir

Değerler kataloğa olduğu gibi girilir; "—" = boş (NULL). "İzleme" = bilinçli olarak eşiksiz (gerekçe §3'te).
Güven: **Y** yüksek · **O** orta · **D** düşük.

| Kod | KPI | Birim | Yön | **Hedef** | **Uyarı** | **Kritik** | Uygulandığı değer | Dayanak (özet) | Güven | Yan etki / oyunlaştırma riski |
|---|---|---|---|---|---|---|---|---|---|---|
| KPI-001 | Personel Devir Oranı | % | lower_better ✔ | 35 | 45 | 60 | **Yıllıklaştırılmış** | ISO 30414/GRI 401-1 tanımı; proje sonu ayrılışları dahil → inşaat için yargı | D | Proje kapanışını erteleme, ayrılışı "iç transfer" gösterme; demobilizasyon ayında yıllıklaştırma sıçraması |
| KPI-002 | Personel Devamlılık Oranı | % | higher_better ✔ | 75 | 65 | 55 | Ham değer; **yalnızca 12 aylık pencerede anlamlı** | ISO 30414; KPI-001 ile tutarlı kalibrasyon | D | Kısa pencerede yapay "yeşil"; işe yaramayan çalışanı tutma |
| KPI-003 | Devamsızlık Oranı (D-3 tanımı) | % | lower_better ✔ | 7 | 9 | 11 | Ham oran; 12 aylık kayan pencerede okunmalı | 4857 md. 53 yıllık izin hakkı + hastalık + diğer bileşenlerin hesabı (§3.2) | D | ⚖️ Yıllık izin kullandırmama, hasta/yaralı çalışanı işe zorlama (presenteeism) |
| KPI-004 | İş Gücü Kapasite Kullanımı | % | higher_better ✔ | 85 | 80 | 70 | Ham oran | Sektör pratiği (iş paketine yazılan saat); yargı | D | Boş/bekleme saatini üretken yazma; KPI-034 ile birlikte okunmalı |
| KPI-005 | İş Gücü Verimlilik Endeksi | index | higher_better ✔ | 100 | 90 | 80 | Ham endeks | Tanım gereği 100 = standart süre; mevcut değerler korundu | O | Standartları gevşetme; hız baskısı → İSG riski |
| KPI-006 | İş Gücü Plan Sapması | persons | neutral ✔ | — | — | — | İzleme | Hem fazla hem eksik kötü; motor "bant" desteklemiyor | — | — |
| KPI-007 | Fazla Çalışma Oranı | % | lower_better ✔ | 5 | 8 | 10 | Ham oran | 4857 md. 41: yıllık 270 saat → üst sınırda pay ≈ %10,8 | O | Fazla çalışmayı kayıt dışı bırakma / serbest zamana kaydırma; ortalama bireysel aşımı gizler ⚖️ |
| KPI-008 | Çalışan Başına Eğitim Saati | hours | higher_better ✔ | 30 | 20 | 16 | **Yıllıklaştırılmış** | İSG Eğitimleri Yönetmeliği: çok tehlikeli sınıf yılda ≥16 saat; GRI 404-1 | O | Düşük kaliteli/şişirilmiş eğitim kaydı ⚖️ |
| KPI-009 | Yetkinlik Kapsama Oranı | % | higher_better ✔ | 100 | 98 | 95 | Dönem sonu ham oran | Zorunlu yetkinlik (MYK belgesi, operatör, yüksekte çalışma) yasal ön şart | O | Yetkinliği "zorunlu değil" diye yeniden sınıflama ⚖️ |
| KPI-010 | İş Gücü Birim Maliyeti | TRY/unit | lower_better ✔ | — | — | — | İzleme | Farklı çıktı birimleri (m³, ton, m²) tek eşikle karşılaştırılamaz; TRY enflasyonu | — | — |
| KPI-011 | İşe Alım Süresi | days | lower_better ✔ | 30 | 45 | 60 | Ham ortalama | ISO 30414 (time to fill); mavi/beyaz yaka karışımı → yargı | D | Talep onayını geciktirme (saat onayla başlar), kaliteden ödün → KPI-018 |
| KPI-013 | Yeniden İş Yapma Oranı | % | lower_better ✔ | 3 | 5 | 8 | Ham oran | Sektör literatürü (yeniden işin maliyet payı); uyarı/kritik korundu | D | Yeniden işi normal iş olarak kaydetme |
| KPI-014A | LTIFR | per 1M h | lower_better ✔ | 1.5 | 3.0 | 5.0 | Ham oran; 12 ay kayan, ≥1M saat | GRI 403-9 / ISO 45001; 6331 md. 14 + 5510 md. 13 bildirim yükümlülüğü | D | ⚖️ **Az bildirme**, kayıp günlü kazayı "hafif iş"e çevirme (§3.3) |
| KPI-014B | TRIR | per 200k h | lower_better ✔ | 1.0 | 2.0 | 3.5 | Ham oran; 12 ay kayan, ≥200k saat | GRI 403-9 (oran tabanı açıklanmalı); OSHA tanımı; KPI-014A ile tutarlı | D | ⚖️ Tıbbi müdahaleyi "ilk yardım"a düşürme; az bildirme |
| KPI-016 | Aktif Personel Sayısı | persons | neutral ✔ | — | — | — | İzleme | Büyüklük göstergesi; plan karşılaştırması KPI-006'da | — | — |
| KPI-017 | İşe Giriş Sayısı | persons | neutral ✔ | — | — | — | İzleme | Proje fazına bağlı; iyi/kötü değil | — | — |
| KPI-018 | İlk 90 Gün Ayrılış Oranı | % | lower_better ✔ | 10 | 15 | 20 | Kohort oranı (yıllıklaştırılmaz) | 4857 md. 15 deneme süresi (≤2 ay, TİS ile ≤4 ay); yargı | D | Uygun olmayan çalışanı 90. günden sonraya kadar tutma; kısa süreli işe alımı alt yükleniciye kaydırma |
| KPI-019 | Çalışan Başına İşgücü Maliyeti | TRY | neutral ✔ | — | — | — | İzleme | TRY enflasyonu, ücret/ülke karışımı; bütçe bazlı hedef bütçe gelince | — | — |
| KPI-020 | Gönüllü Devir Oranı | % | lower_better ✔ | 12 | 18 | 25 | **Yıllıklaştırılmış** | ISO 30414 / GRI 401-1; kontrol edilebilir devir; yargı | D | Gönüllü ayrılışı "sözleşme sonu"/"bilinmiyor" olarak kodlama |
| KPI-021 | İlk 30 Gün Ayrılış Oranı | % | lower_better ✔ | 3 | 5 | 8 | Kohort oranı | Oryantasyon/işe başlatma kalitesi; yargı | D | Aynı (KPI-018) |
| KPI-022 | İlk 180 Gün Ayrılış Oranı | % | lower_better ✔ | 18 | 25 | 32 | Kohort oranı | KPI-018/021 ile iç içe kohort tutarlılığı; yargı | D | Aynı (KPI-018) |
| KPI-023 | Ortalama Kıdem | years | neutral ✔ | — | — | — | İzleme | Proje işinde yüksek kıdem kendiliğinden iyi değil; kıdem tazminatı/izin yükümlülüğü göstergesi | — | — |
| KPI-028 | Hastalık Devamsızlık Oranı | % | lower_better ✔ | 2.0 | 3.0 | 4.5 | Ham oran; 12 ay kayan | ISO 30414; sektör pratiği; yargı | D | ⚖️ Rapor almayı caydırma, iş kazası raporunu gizleme (LTIFR ile bağlantılı) |
| KPI-032 | Gece Çalışması Oranı | % | lower_better ✔ | 5 | 10 | 15 | Ham oran | 4857 md. 69 (gece ≤7,5 saat, sağlık gözetimi); yorgunluk riski; yargı | D | Gece saatlerini gündüz yazma; tünel/trafik altı projelerde yapısal kırmızı |
| KPI-034 | Duruş Kaynaklı Kayıp Saat Oranı | % | lower_better ✔ | — | — | — | İzleme (12 ay) | Kayıt disiplini oturmadan eşik dürüst kaydı cezalandırır | — | — |
| KPI-036 | Fazla Mesai Maliyet Oranı | % | lower_better ✔ | 7 | 11 | 14 | Ham oran | 4857 md. 41 (%50 zamlı ücret) ile KPI-007 eşiklerinden türetildi | D–O | Fazla çalışmayı ücret dışı kalemlere / serbest zamana kaydırma |
| KPI-048 | Kadın Çalışan Oranı | % | neutral ✔ | — | — | — | İzleme | Hedef bir yönetim kurulu D&I kararıdır; ⚖️ 4857 md. 5, Anayasa md. 10 | — | — |
| KPI-050 | Alt Yüklenici İş Gücü Payı | % | neutral ✔ | — | — | — | İzleme | İş modeli kararı; ⚖️ 4857 md. 2 / Alt İşverenlik Yönetmeliği (muvazaa riski) | — | — |

**Özet:** 19 KPI'ya sayısal eşik, 9 KPI bilinçli olarak yalnızca izleme (006, 010, 016, 017, 019, 023, 034, 048, 050). Hiçbir KPI'da yön değişikliği önerilmiyor (KPI-006 için "bant" yönü önerisi §5'te).

**Mevcut katalog değerinden değişenler:** KPI-001 (—/30/45 → 35/45/60), KPI-002 (80/70/60 → 75/65/55), KPI-007 (—/10/15 → 5/8/10), KPI-008 (24/16/8 → 30/20/16), KPI-009 (95/90/80 → 100/98/95), KPI-013 (—/5/8 → 3/5/8). KPI-005 aynen korundu.

---

## 3. KPI bazında gerekçeler

### 3.1 İş gücü istikrarı (KPI-001, 002, 018, 020, 021, 022)

- **KPI-001 Toplam devir (yıllık 35 / 45 / 60).** Payda ortalama kişi sayısı, pay proje sonu ve sözleşme sonu dahil tüm ayrılışlar. Proje bazlı inşaat organizasyonunda bu oranın %40–80 bandında olması olağandır; ofis kıyasları (%10–15) burada yanlıştır. Değerler bir yargıdır; amaç demobilizasyon dışındaki istikrarsızlığı yakalamaktır. **Okuma kuralı:** proje/firma kapsamında, demobilizasyon fazındaki bir projenin kırmızı görünmesi beklenen bir durumdur; yorum KPI-020 (gönüllü) ve ayrılış kategorisi kırılımı ile yapılmalıdır. Aylık görünümde yıllıklaştırma (×365/30) tek bir toplu çıkış ayını abartır → yönetim raporlarında 12 aylık kayan pencere kullanılmalı.
- **KPI-020 Gönüllü devir (yıllık 12 / 18 / 25).** İnsan kaynağının etkileyebileceği kısım budur (ücret farkı, konaklama/yemek koşulları, ustabaşı ilişkisi). Mavi yakada günlük ücret farkı için iş değiştirme yaygın olduğundan ofis kıyaslarından yüksek tutuldu. KPI-001 eşiklerinin altında, onunla tutarlı. **Veri kalitesi koşulu:** kategorisi boş ayrılışlar gönüllü sayılmaz (katalog kuralı); boş kategori oranı %5'i aşarsa KPI-020 düşük görünür ve güvenilmez — SGK işten çıkış kodlarının kategori eşlemesi zorunlu.
- **KPI-002 Devamlılık (75 / 65 / 55).** Yalnızca **12 aylık pencerede** anlamlıdır: aylık pencerede başlangıç nüfusunun ~%95+'ı ay sonunda hâlâ çalışır ve gösterge her zaman yeşil kalır (sahte rahatlık). Mevcut 80/70/60, proje sonu ayrılışlarını içeren bir tanım için iddialı; 5 puan aşağı çekildi. KPI-001 ile kaba tutarlılık: yıllık devir %35 iken başlangıç nüfusunun ~%75'inin kalması, devrin bir kısmının yıl içinde işe girip çıkanlardan oluştuğu bir sektörde makul.
- **KPI-021 / 018 / 022 Erken ayrılış (30 g: 3/5/8 · 90 g: 10/15/20 · 180 g: 18/25/32).** Kohortlar iç içe olduğundan değerler zorunlu olarak artan sırada (30 < 90 < 180) verildi. 30 gün = işe alım/oryantasyon/barınma koşulları; 90 gün = deneme süresi (4857 md. 15: en çok 2 ay, TİS ile 4 ay) sonrası; 180 gün = ilk proje fazı uyumu. **Tanım sorunu:** pay, kasıtlı kısa süreli (belirli süreli, iş paketi bazlı) sözleşmelerin "sözleşme sonu/proje sonu" bitişlerini de içerir. Demo veride 90 gün içinde ayrılanların ~%39'u bu iki kategoridedir (sentetik veri, yalnızca yapıyı gösterir). Gerçek veride bu oran yüksekse eşikler anlamını yitirir → §5 madde 3. Son 30/90/180 günün kohortları henüz tam gözlenmemiştir; motor not düşer ve değer sonradan yükselir — güncel dönemlerin "yeşil"i geçicidir.

### 3.2 Devamsızlık (KPI-003, KPI-028)

**KPI-003 (D-3 tanımı: habersiz + raporlu + yıllık izin + ücretsiz izin + diğer izinler; resmî tatil ve doğum/ebeveyn izni hariç) — 7 / 9 / 11.**

Bu tanım, ISO 30414 / yaygın Avrupa pratiğinden farklı olarak **planlı ve yasal hak olan yıllık izni** içerir; yani KPI-003 bir "plansız devamsızlık" değil, **"işte olmama oranı"**dır. Eşikler bu tanıma göre bileşen bileşen kuruldu (planlanan süre: haftalık 45 saat, 4857 md. 63; 6 günlük çalışma haftasında ~7,5 saat/gün; yılda ~2.200–2.250 planlı saat):

| Bileşen | Beklenen yıllık pay (yargı aralığı) | Dayanak |
|---|---|---|
| Yıllık izin | %3–5 (karma kıdem); kıdemli kadroda %6–7 | 4857 md. 53: 1–5 yıl 14 gün, 5–15 yıl 20 gün, 15+ yıl 26 gün; 18 yaş altı/50 yaş üstü ≥20 gün; 1 yılı doldurmayanın hakkı yok |
| Raporlu gün (hastalık) | %1,5–2,5 | KPI-028 ile aynı bileşen |
| Habersiz devamsızlık | %0,3–1 | Sektör pratiği |
| Ücretsiz + diğer izinler | %0,5–1,5 | Ek md. 2 mazeret izinleri (evlenme, ölüm vb.), md. 27 iş arama izni vb. |
| **Toplam** | **≈ %5,5–9** | |

- **Hedef 7:** karma kıdemli bir kadroda tüm yasal izinlerin kullanıldığı ve plansız devamsızlığın düşük olduğu durum.
- **Uyarı 9:** kıdemli (20 gün izin hakkı olan) bir birimin yalnızca yasal izin + normal hastalık ile ulaşabileceği üst değerin hemen üstü — **yasal izin kullanımının tek başına sarıya düşürmemesi** için bilerek bu düzeyde.
- **Kritik 11:** plansız devamsızlığın belirgin yükseldiği düzey.
- **Okuma kuralı:** aylık görünüm mevsimseldir (yaz ayları, bayram köprüleri, kış duruşunda toplu izin); eşik kararı **12 aylık kayan pencerede** verilmelidir. Aylık kırmızı → önce `included_hours_by_type` kırılımına bakılır.
- ⚖️ **Teşvik riski (yüksek):** yıllık izin hakkından feragat edilemez ve izin bölünerek bile olsa kullandırılmalıdır (4857 md. 53, 56; Yıllık Ücretli İzin Yönetmeliği). Düşük bir KPI-003 hedefi, yöneticileri izin kullandırmamaya / izni fesihte paraya çevirmeye (md. 59) itebilir; bu hem hukuka aykırı hem de yorgunluk kaynaklı İSG riskidir (6331). **KPI-003 hiçbir yöneticinin primine bağlanmamalı.**
- **Öneri (katalog değişikliği gerektirir, Orchestrator/ADR):** hedeflenebilir asıl gösterge olarak aynı hesaplayıcıyla `absence_types = ["unexcused", "sick"]` parametreli ayrı bir **"Plansız Devamsızlık Oranı"** KPI'sı açılması (önerilen başlangıç: 2.5 / 3.5 / 5). KPI-003 o zaman toplam "işte olmama" izleme göstergesi olarak kalır.

**KPI-028 Hastalık devamsızlığı — 2.0 / 3.0 / 4.5.** Yalnızca `sick` (raporlu) saatler. Değerler yargıdır; Türkiye'de raporlu gün düzeyi rapor alma pratiğine, işverenin ilk iki gün ödeme politikasına ve TİS'e bağlıdır. ⚖️ Risk: hasta/yaralı çalışanın işe çağrılması (presenteeism) ve **iş kazası istirahat raporlarının bu kalemde görünmesi** — KPI-028'i düşürme baskısı, kayıp günlü kazaların gizlenmesine (KPI-014A) dönüşebilir. Yalnızca toplu düzeyde kullanılmalı; tanı/kişi bilgisi tutulmaz (SECURITY_PRIVACY.md ile uyumlu).

### 3.3 İş sağlığı ve güvenliği (KPI-014A, KPI-014B)

| | Hedef | Uyarı | Kritik | per 1M h karşılığı |
|---|---|---|---|---|
| KPI-014A LTIFR (per 1M h) | 1.5 | 3.0 | 5.0 | — |
| KPI-014B TRIR (per 200k h) | 1.0 | 2.0 | 3.5 | 5 / 10 / 17,5 |

- **Tutarlılık:** TRIR'ın 1M saate çevrilmiş karşılığı LTIFR'ın ~3,3–3,5 katıdır (kaydedilebilir olay / kayıp günlü kaza oranı); tipik saha yapısıyla uyumludur. Bu oran gerçek veride 2'nin altına düşerse **sınıflandırma sorunu** (tıbbi müdahalelerin ilk yardıma düşürülmesi) şüphesi doğar.
- **Dayanak:** GRI 403-9 ve ESRS S1-14 oranların açıklanmasını ister, hedef vermez. Değerler, uluslararası çalışan ana yüklenicilerin hedef bandı ile Türkiye inşaat sektörünün yüksek kaza sıklığı (SGK istatistikleri) arasında **yargıdır**; güven düşük. Gerçek geçmiş yüklendiğinde ilk yıl hedefi "son 3 yıl ortalamasının ~%20 altı" olarak yeniden kurulmalı.
- **Bildirimi caydırmama kuralları (eşiklerin onay koşulu):**
  1. İSG oranları **bireysel veya ekip primine, ustabaşı/şantiye şefi değerlendirmesine bağlanmaz.** Alt yüklenici değerlendirmesinde oranla birlikte **bildirim kalitesi** (ramak kala sayısı, bildirim süresi) puanlanır.
  2. Kayıp günlü kaza oranındaki düşüş, **ramak kala bildirimlerinde (KPI-043, taslak) eşzamanlı düşüşle** birlikteyse "iyileşme" değil **veri kalitesi alarmı** sayılır. KPI-043'ün aktive edilmesi bu eşiklerin ön koşulu olarak önerilir.
  3. Bildirim kültürünü güçlendirme kampanyalarından sonraki ilk 6–12 ayda oranın yükselmesi başarısızlık sayılmaz.
  4. `restricted_work` / `lost_time_injury` oranı izlenir; "hafif iş" görevlendirmesinin kayıp günlü kazayı gizlemek için kullanılması önlenir.
  5. **Herhangi bir ölümlü kaza**, orandan bağımsız olarak kritik olaydır (motor bunu oranla ifade edemez → ayrı uyarı gerekir, §5).
- **Küçük payda:** 100 bin saatlik bir proje ayında tek bir kayıp günlü kaza LTIFR'ı 10'a çıkarır. Durum değerlendirmesi **12 aylık kayan pencerede** ve LTIFR için ≥1M saat, TRIR için ≥200 bin saat çalışılmış kapsamlarda yapılmalı.
- ⚖️ İş kazası 3 iş günü içinde SGK'ya bildirilmelidir (6331 md. 14, 5510 md. 13); kayıt dışı bırakmayı teşvik eden her hedef hukuki risk doğurur.

### 3.4 Çalışma süresi ve maliyet (KPI-007, KPI-032, KPI-036)

- **KPI-007 Fazla çalışma — 5 / 8 / 10.** Pay fazla çalışma saati, payda fazla çalışma dahil toplam çalışılan saat. 4857 md. 41 yıllık üst sınırı 270 saattir. Normal süre ≈ 45 saat × ~49,5 hafta ≈ 2.230 saat ise üst sınırdaki bir çalışanın payı 270 / (2.230 + 270) ≈ **%10,8**'dir. Organizasyon ortalaması %10'a ulaştıysa, ortalama dağılım gereği çalışanların önemli kısmı yasal sınırda veya üstündedir → **kritik 10** (mevcut 15 yasal olarak mümkün olanın üstündeydi). Uyarı 8, hedef 5 (yoğun dönemler dahil sürdürülebilir düzey). Aylık zirveler (beton dökümü, teslim öncesi) tek başına sorun değildir; sınır yıllıktır.
  - ⚖️ Ortalama, **kişi bazlı 270 saat aşımını göstermez**; uyum için KPI-031 (taslak, "Yıllık Fazla Mesai Limit Aşımı") aktive edilmeli. Günlük 11 saat sınırı (md. 63) ve **yer altı/tünel işleri gibi günde 7,5 saat ve daha az çalışılması gereken işlerde fazla çalışma yasağı** (md. 41 ve ilgili yönetmelik) bu oranla izlenemez.
  - ⚖️ **Rusya'daki iki alt yüklenici (`country = RU`)**: Rusya İş Kanunu'nda yıllık fazla çalışma sınırı 120 saattir (TK RF md. 99 — hukuk teyidi gerekir); bu varlıklar için %10 kritik eşik çok gevşektir (120 / ~2.000 ≈ %5,7). Ülke bazlı eşik desteği yoktur (§5).
- **KPI-036 Fazla mesai maliyet payı — 7 / 11 / 14.** Fazla çalışma saatinin ücreti %50 zamlıdır (md. 41). Saat payı *h* için ücret payı ≈ 1,5h / (1 − h + 1,5h): h = %5 → %7,3; h = %8 → %11,5; h = %10 → %14,3. Payda ücret dışı işveren maliyetlerini (yan haklar, konaklama, yemek, ulaşım) da içerdiğinden gerçek pay biraz daha düşük olur; eşikler bu nedenle KPI-007 ile tutarlı ve hafif yuvarlanmış. Serbest zaman kullandırılan fazla çalışma (md. 41/4) maliyette görünmez → iki KPI farklılaşırsa nedeni bu olabilir.
- **KPI-032 Gece çalışması payı — 5 / 10 / 15.** Gece çalışması yorgunluk ve kaza riskini artırır; 4857 md. 69 gece çalışmasını 7,5 saatle sınırlar, gece çalışanları için sağlık gözetimi ve (kadın çalışanlar için) ayrı yönetmelik koşulları vardır. Değerler yargıdır. **Tünel, trafik altında yol ve gece beton dökümü gibi projelerde gece çalışması yapısaldır**; bu projeler kırmızı görünecektir → proje bazlı eşik gerekir (§5). ⚖️ Post değişimi (en çok iki haftada bir) ve kişi bazlı 7,5 saat sınırı bu oranla izlenemez.

### 3.5 Verimlilik ve kalite (KPI-004, 005, 006, 013, 034, 010)

- **KPI-004 Kapasite kullanımı — 85 / 80 / 70.** "Üretken saat" = iş paketine yazılan saat; bu, saha literatüründeki "wrench time"dan (%40–60) çok daha geniştir. Yargı; güven düşük. **KPI-034 ile birlikte okunmalı:** boş bekleme saatlerinin iş paketine yazılması bu oranı yükseltir ama gerçeği gizler.
- **KPI-005 Verimlilik endeksi — 100 / 90 / 80 (değişmedi).** 100, kalite onaylı çıktının standart süresine eşittir; tanım gereği doğru hedeftir. Güven orta: değerin anlamı standartların (birim fiyat analizleri / şirket normları) nasıl kurulduğuna bağlı. Hız baskısının İSG'ye etkisi izlenmeli; endeks bireysel performans puanı olarak kullanılmamalı.
- **KPI-013 Yeniden iş — 3 / 5 / 8.** Mevcut uyarı/kritik korundu, hedef 3 eklendi. Literatürde yeniden işin proje maliyetindeki payı genellikle %5–12 aralığında verilir; işçilik saati payı olarak daha düşük beklenir. Asıl risk yeniden işin kaydedilmemesidir.
- **KPI-034 Duruş kayıp saati — izleme (12 ay).** Yön doğru (lower_better), ancak duruş (hava, malzeme, ekipman, tasarım bekleme) kaydı henüz oturmamışken eşik koymak **dürüst kayıt yapan şantiyeyi cezalandırır** (İSG'deki az bildirme sorununun aynısı). Demo değerin (%0,12) çok düşük olması kayıt eksikliğinin nasıl görüneceğine iyi bir örnektir. 12 aylık gerçek kayıttan sonra eşik önerilecek.
- **KPI-006 Plan sapması — izleme.** Hem fazla hem eksik kadro kötüdür; doğru eşik ±%5 / ±%10 gibi bir **bant**tır, motor bunu desteklemez (§5).
- **KPI-010 Birim işçilik maliyeti — izleme.** Çıktı birimleri (m³ beton, ton donatı, m² kalıp) farklıdır; tek bir TRY/birim eşiği anlamsızdır ve TRY enflasyonu sabit eşiği aylar içinde eskitir. Hedef, iş paketi düzeyinde proje bütçesinden (birim fiyat) gelmelidir.

### 3.6 Gelişim (KPI-008, KPI-009)

- **KPI-008 Eğitim saati (yıllık 30 / 20 / 16).** Çalışanların İSG Eğitimlerinin Usul ve Esasları Hakkında Yönetmelik'e göre **çok tehlikeli** sınıfta (inşaat işlerinin çoğu) çalışan başına en az 16 saat eğitim ve yılda en az bir yenileme gerekir → **kritik = yasal asgari (16)**. Ortalama 16'nın altındaysa, en az bir grup yasal eğitimi almamış demektir. Mevcut 24/16/8'deki kritik 8, yasal asgarinin altındaydı. Hedef 30 = yasal İSG + mesleki/teknik eğitim. Yüksek devirde yeni girenlerin işe başlangıç eğitimi ortalamayı yükseltir; bu, gelişim değil zorunluluktur — kırılımda İSG/mesleki ayrımı yapılmalı.
  - ⚖️ Ortalama, kişi bazlı uyumu kanıtlamaz (KPI-040/045 taslak). Alt yüklenici çalışanlarının eğitiminden asıl işveren de sorumluluk paylaşır (4857 md. 2, 6331 md. 22 koordinasyon) — kapsam hukukla netleştirilmeli.
- **KPI-009 Yetkinlik kapsama (100 / 98 / 95).** Paydada yalnızca **zorunlu** yetkinliği olan görevlendirmeler vardır (MYK mesleki yeterlilik belgesi zorunlu meslekler, iş makinesi operatör belgesi, yüksekte çalışma vb.). Zorunlu belgesi olmayan kişinin o işte çalıştırılması yasal ve İSG açığıdır → hedef %100. Uyarı 98 / kritik 95, veri girişi gecikmeleri ve belge yenileme süreçleri için tolerans. Mevcut 95/90/80 bu açığı fazla tolere ediyordu.
  - ⚖️ Risk: bir yetkinliği "zorunlu değil" diye yeniden sınıflamak oranı yükseltir. `occupation_competencies` zorunluluk değişiklikleri onaylı ve denetim izli olmalı; zorunlu listesinin yasal dayanağı hukuk/İSG ile teyit edilmeli.

### 3.7 İşe alım (KPI-011)

**30 / 45 / 60 gün.** Saat talep onayından teklif kabulüne kadar işler. Mavi yaka saha işe alımı günler, mühendis/şef pozisyonları 45–90 gün sürer; karma ortalama yargıdır ve meslek grubuna göre okunmalıdır. Risk: talebin geç onaylanması (saati sıfırlar), hızlı ama yanlış işe alım → KPI-018/021 ile birlikte okunmalı.

### 3.8 Yalnızca izleme — gerekçeler (KPI-016, 017, 019, 023, 048, 050)

| Kod | Neden eşik yok |
|---|---|
| KPI-016 Aktif personel | Büyüklük; iyi/kötü değil. Plan karşılaştırması KPI-006'dadır |
| KPI-017 İşe giriş | Proje fazına (mobilizasyon) bağlı; yüksek giriş ne iyi ne kötü |
| KPI-019 Çalışan başına maliyet | TRY enflasyonu, ülke ve meslek karışımı; ayrıca dönem uzunluğuna bağlı ve yıllıklaştırılmıyor (§5). Hedef bütçeden gelmeli |
| KPI-023 Ortalama kıdem | Proje işinde yüksek kıdem kendiliğinden hedef değildir; ancak kıdem tazminatı ve yıllık izin yükümlülüğünün (KPI-003 beklentisi) göstergesidir |
| KPI-048 Kadın çalışan oranı | Sayısal hedef, yönetim kurulunun D&I politikası kararıdır; saha/ofis ayrımı olmadan tek hedef yanıltıcıdır. ⚖️ Pozitif ayrımcılık tedbirleri Anayasa md. 10 kapsamında mümkündür, ancak hedefin işe alım kararlarında kullanılma biçimi 4857 md. 5 (eşit davranma) açısından hukukla değerlendirilmelidir |
| KPI-050 Alt yüklenici payı | İş modeli kararıdır. ⚖️ Çok yüksek pay, asıl işin bölünerek verilmesi / muvazaa riskini (4857 md. 2, Alt İşverenlik Yönetmeliği) gündeme getirebilir; bu bir eşik değil hukuki inceleme konusudur |

---

## 4. Politika sorusu B-26 — Doğum / ebeveyn izni KPI-003'e dahil edilmeli mi?

**Karar önerisi: HAYIR. Doğum, ebeveyn ve bunlarla bağlantılı tüm yasal korumalı izinler KPI-003 ve KPI-028'den kalıcı olarak hariç tutulmalı; işgücü planlaması için ayrı, eşiksiz ve yalnızca toplu bir izleme göstergesiyle görünür kılınmalıdır (Seçenek C).** ⚖️ Hukuk onayı gerekir.

### Seçenekler

| Seçenek | Açıklama | Sonuç |
|---|---|---|
| A — Hariç (D-3 geçici durumu) | KPI-003'ten hariç, başka yerde görünmez | Ayrımcılık riski yok; ancak uzun süreli kapasite kaybı planlamada görünmez |
| B — Dahil | KPI-003 payına eklenir | Kadın çalışanı fazla olan birimlerde KPI-003 yapısal olarak yükselir; eşik/hedef baskısı doğurgan yaştaki kadınları işe almamaya veya izin kullananları dışlamaya iter → **dolaylı ayrımcılık** riski. Birimler arası karşılaştırma bozulur |
| **C — Hariç + ayrı izleme (önerilen)** | KPI-003/028'den hariç; "Yasal korumalı izin saatleri" ayrı, `neutral`, eşiksiz, yalnızca toplu (min. grup boyutu uygulanır) bir planlama göstergesi | Ayrımcılık riski yok; kapasite planlaması (yerine geçici personel) yapılabilir |

### Gerekçe ve ⚖️ hukuki işaretler

- ⚖️ **4857 md. 74** doğum öncesi ve sonrası analık iznini (toplam 16 hafta; çoğul gebelikte +2 hafta), isteğe bağlı 6 aya kadar ücretsiz izni, doğum sonrası yarım çalışma dönemini, gebelikte periyodik kontroller için ücretli izni ve 1 yaş altı çocuk için **günde 1,5 saat süt iznini** düzenler. **Ek md. 2** babalık izni (5 gün) ve evlat edinme iznini, ayrıca engelli/kronik hastalığı olan çocuk için ebeveyn iznini düzenler. *Süreler güncel mevzuata göre hukuk birimince teyit edilmeli (2025–2026'da bu sürelerin uzatılmasına ilişkin düzenlemeler gündemdeydi).*
- ⚖️ **4857 md. 5** (cinsiyet ve gebelik nedeniyle farklı işlem yasağı), **md. 18** (gebelik/doğum geçerli fesih nedeni değildir), **6701 sayılı TİHEK Kanunu** (doğrudan ve dolaylı ayrımcılık yasağı), Anayasa md. 10 ve 50, Türkiye'nin taraf olduğu CEDAW. Bir yöneticinin hedefini bu izinler nedeniyle kaçırması, işe alım ve görevlendirme kararlarını dolaylı olarak etkiler.
- ⚖️ **KVKK md. 6:** gebelik/doğumla ilgili izin türü sağlık verisi niteliği taşıyabilir (özel nitelikli kişisel veri). Ayrı izleme göstergesi yalnızca toplu, minimum grup boyutu altında bastırılmış ve rol kısıtlı olmalıdır.

### Uygulama için kritik veri eşlemesi (yoksa karar kâğıt üzerinde kalır)

Mevcut parametre `["unexcused", "sick", "annual", "unpaid", "other"]` olduğundan, aşağıdaki kayıtlar `parental` yerine yanlış türe aktarılırsa **farkında olmadan KPI-003'e girer**:

| Kayıt | Yanlış düşebileceği tür | Doğru eşleme |
|---|---|---|
| SGK **analık** geçici iş göremezlik raporu (doğum öncesi/sonrası) | `sick` | `parental` |
| md. 74 doğum sonrası **6 aya kadar ücretsiz izin** | `unpaid` | `parental` |
| **Süt izni** (günde 1,5 saat), yarım çalışma dönemi | `other` / planlı saatte düşüş | `parental` (veya planlı saatten düşülmeli) |
| Gebelik kontrolleri ücretli izni | `other` | `parental` |
| **Babalık izni**, evlat edinme izni, engelli çocuk için ebeveyn izni (Ek md. 2) | `other` | `parental` |

Bu eşleme import hattında (Agent 08) kural olarak tanımlanmalı ve QA (Agent 12) tarafından testle doğrulanmalıdır.

### İlgili ikincil öneri
D-3 tanımı yıllık izni ve raporlu günü de içerir; bunlar da yasal haklardır. Bu nedenle KPI-003 ve KPI-028 **hiçbir bireysel değerlendirme, prim veya disiplin sürecinde kullanılmamalı**; yalnızca organizasyon düzeyinde kapasite ve iş koşulu göstergesi olarak okunmalıdır.

---

## 5. Katalogda bulduğum tanım sorunları

1. **KPI-003 tanımı standart dışı.** Yıllık izin (yasal hak) "devamsızlık" sayılıyor; ISO 30414 pratiği genelde planlı izni hariç tutar. Hedeflenebilir gösterge için ayrı "Plansız Devamsızlık" KPI'sı önerildi (§3.2). Ayrıca: `other` türü yasal mazeret izinlerini, `sick` türü iş kazası istirahatini ve (yanlış eşlemede) analık raporunu içerebilir (§4).
2. **KPI-003/028 paydası.** Payda "onaylı puantajdaki planlı saat"tir. Gerçek kaynakta izinli günler için puantaj satırı/planlı saat oluşmuyorsa oran şişer veya izin günleri paydadan düşer. Kaynak sistemin izin günlerinde planlı saati nasıl yazdığı doğrulanmalı (demo puantajı haftalık satırlardan oluşuyor; gerçek veri farklı olabilir).
3. **KPI-018/021/022 kasıtlı kısa sözleşmeleri içeriyor.** "Sözleşme sonu / proje sonu" bitişleri erken ayrılış sayılıyor. Öneri: `exclude_categories: ["contract_end", "project_end"]` parametresi veya planlanmış kısa süreli sözleşme işareti (yeni formül versiyonu → ADR).
4. **KPI-002 dönem uzunluğuna bağlı, yıllıklaştırılmıyor.** Aynı eşik aylık ve yıllık pencerede farklı anlam taşır; aylık pencerede gösterge hep yeşil. Öneri: durum yalnızca ≥12 aylık pencerede hesaplansın ya da dönem uzunluğu parametresi eklensin.
5. **KPI-019 dönem uzunluğuna bağlı.** 1 aylık pencerede aylık, 12 aylıkta yıllık maliyet verir ama birim yalnızca "TRY". Nötr olduğu için durum üretmez; yine de etiket "TRY / dönem" veya yıllıklaştırma ile netleşmeli.
6. **Nötr KPI'da bant eşiği yok.** KPI-006 için doğru model "0'a yakın iyi" (±%5 / ±%10) bandıdır; motor yalnızca lower/higher_better destekler.
7. **Eşikler kapsamdan bağımsız (tek küresel set).** Gece çalışması (tünel projeleri), toplam devir (demobilizasyon fazı), fazla çalışma (TR 270 saat ↔ RU 120 saat) proje/ülke bazlı eşik gerektirir. Ülke veya proje bazlı eşik desteği önerilir.
8. **İSG oranlarında küçük payda.** Proje-ay düzeyinde tek olay oranı fırlatır; minimum saat koşulu (LTIFR ≥1M h, TRIR ≥200k h) veya 12 aylık kayan pencere olmadan durum rengi yanıltıcı. Ölümlü kaza için orandan bağımsız ayrı uyarı yok.
9. **KPI-014A/B bildirim kalitesi göstergesi yok.** KPI-043 (Ramak Kala Bildirim Oranı) taslak; İSG eşiklerinin güvenli kullanımı için aktive edilmesi önerilir.
10. **KPI-007 bireysel uyumu göstermez.** Yasal uyum için KPI-031 (taslak) gerekir; ortalama, 270 saati aşan kişileri gizler.
11. **KPI-009 manipülasyona açık payda.** Zorunlu yetkinlik tanımının değiştirilmesi oranı doğrudan değiştirir; değişiklik kontrolü/denetim izi gerekir.
12. **KPI-020 boş kategori.** Kategorisi boş ayrılışlar gönüllü sayılmıyor (doğru tercih), ancak boş oranı yüksekse KPI-020 sistematik olarak düşük kalır; veri kalite puanına bu oran yansıtılmalı.

---

## 6. Gerçek organizasyondan ihtiyacım olanlar

Bu değerler kesinleşmeden önce **yalnızca şirketin sağlayabileceği** veri ve kararlar:

### 6.1 Veri (en az 24, tercihen 36 ay gerçek geçmiş)
- **İstihdam hareketleri:** tüm işe giriş/çıkışlar, **SGK işten çıkış kodlarının** platform kategorilerine (gönüllü, işveren feshi, sözleşme sonu, proje sonu, emeklilik…) onaylı eşlemesi; belirli/belirsiz süreli ayrımı ve planlanmış kısa süreli sözleşme işareti.
- **Sözleşme ve kadro karışımı:** mavi/beyaz yaka, yerli/yabancı, ülke (TR/RU/diğer), ana yüklenici/alt yüklenici, TİS kapsamı; **kıdem dağılımı** (yıllık izin hakkı ve KPI-003 beklentisi için).
- **Puantaj:** haftalık çalışma düzeni (45 saat, 5 veya 6 gün), vardiya/gece düzeni, izinli günlerde planlı saatin nasıl yazıldığı, fazla çalışma onay süreci, serbest zaman kullanımı.
- **İzin kayıtları:** tür bazında; **SGK rapor türleri** (hastalık, iş kazası, meslek hastalığı, analık) ayrı ayrı; ebeveyn izinlerinin §4'teki eşlemesi.
- **İSG:** son 3 yılın olay kayıtları (ölümlü, kayıp günlü, kısıtlı iş, tıbbi müdahale, ilk yardım, ramak kala), şirketin LTI tanımı (kaç gün kayıp?), alt yüklenici saatlerinin dahil olup olmadığı, SGK bildirim kayıtları, işyeri tehlike sınıfları, **müşteri/işveren sözleşmelerindeki İSG hedefleri**.
- **Eğitim ve yetkinlik:** eğitim kayıtları (İSG / mesleki ayrımıyla), meslek bazında zorunlu yetkinlik listesi ve **yasal dayanağı** (MYK tebliğleri, operatör belgeleri).
- **Üretim standartları:** KPI-005 standart sürelerinin kaynağı ve son güncelleme tarihi; yeniden iş ve duruş kayıt pratiği.
- **İşe alım:** talep onay ve teklif kabul tarihleri, meslek grubuna göre.
- **Sektör kıyası:** İNTES / TİSK ve SGK istatistik yıllıklarından sektör (NACE 41–43) devir ve kaza oranları — kıyas grubunun seçimi İK yönetiminin kararıdır.

### 6.2 Bütçe
- İşçilik bütçesi, **fazla mesai bütçesi** (KPI-007/036 hedefi bütçeyle uyumlu olmalı), eğitim bütçesi ve saat planı, onaylı kadro planı (KPI-006), iş paketi bazında birim işçilik maliyeti hedefleri (KPI-010).

### 6.3 Yönetim kararları
1. Bu eşiklerin **prim / performans / alt yüklenici değerlendirmesine bağlanıp bağlanmayacağı** — önerim: İSG ve devamsızlık KPI'ları bağlanmasın; bağlanırsa yalnızca bildirim kalitesiyle birlikte.
2. **B-26** onayı (Seçenek C) ve §4'teki izin türü eşlemesinin import hattına işlenmesi ⚖️.
3. "Plansız Devamsızlık" KPI'sının açılması (yeni KPI → ADR + Orchestrator onayı).
4. Proje/ülke bazlı eşik ihtiyacı (tünel projeleri, RU varlıkları) ve RU iş hukuku sınırlarının teyidi ⚖️.
5. KPI-048 için D&I politikası ve hedefi (yönetim kurulu) ⚖️; KPI-050 için alt yüklenici stratejisi ve muvazaa açısından hukuki görüş ⚖️.
6. KPI-043 (ramak kala) ve KPI-031 (yıllık fazla mesai aşımı) taslaklarının aktivasyon önceliği.
7. Yeniden kalibrasyon takvimi: gerçek veri yüklendikten sonra 3 ay içinde tam gözden geçirme; İSG üç ayda bir, diğerleri yılda bir.

---

## 7. Summary for the Orchestrator (English)

**Status:** HR Director recommendation — requires human HR management approval (and legal review for ⚖️ items) before activation. Values are judgement-based starting points; none is derived from the synthetic demo data.

**Values to apply (target / warning / critical; "—" = NULL):**

| Code | Target | Warning | Critical | Note |
|---|---|---|---|---|
| KPI-001 | 35 | 45 | 60 | annualised |
| KPI-002 | 75 | 65 | 55 | meaningful on 12-month windows only |
| KPI-003 | 7 | 9 | 11 | D-3 definition incl. annual leave |
| KPI-004 | 85 | 80 | 70 | |
| KPI-005 | 100 | 90 | 80 | unchanged |
| KPI-006 | — | — | — | monitoring only |
| KPI-007 | 5 | 8 | 10 | 270 h/yr cap ≈ 10.8 % share |
| KPI-008 | 30 | 20 | 16 | annualised; critical = 16 h legal HSE minimum |
| KPI-009 | 100 | 98 | 95 | |
| KPI-010 | — | — | — | monitoring only |
| KPI-011 | 30 | 45 | 60 | |
| KPI-013 | 3 | 5 | 8 | |
| KPI-014A | 1.5 | 3.0 | 5.0 | conditional on no-under-reporting rules (§3.3) |
| KPI-014B | 1.0 | 2.0 | 3.5 | conditional on no-under-reporting rules (§3.3) |
| KPI-016 | — | — | — | monitoring only |
| KPI-017 | — | — | — | monitoring only |
| KPI-018 | 10 | 15 | 20 | |
| KPI-019 | — | — | — | monitoring only |
| KPI-020 | 12 | 18 | 25 | annualised |
| KPI-021 | 3 | 5 | 8 | |
| KPI-022 | 18 | 25 | 32 | |
| KPI-023 | — | — | — | monitoring only |
| KPI-028 | 2.0 | 3.0 | 4.5 | |
| KPI-032 | 5 | 10 | 15 | |
| KPI-034 | — | — | — | monitoring only for 12 months |
| KPI-036 | 7 | 11 | 14 | derived from KPI-007 with 150 % overtime pay |
| KPI-048 | — | — | — | monitoring only |
| KPI-050 | — | — | — | monitoring only |

No direction changes. All thresholds are coherent with direction (checked against `engine._threshold_status`).

**Needs human / legal confirmation:** all values (HR management); ⚖️ B-26 option C (exclude maternity/parental and related protected leave permanently, track separately as aggregate neutral metric) plus leave-type mapping at import (SGK maternity reports, post-maternity unpaid leave, nursing leave, paternity leave → `parental`); KPI-003 must not be tied to pay (annual leave is non-waivable, 4857 md. 53); safety targets only with the no-under-reporting rules (6331 md. 14 / 5510 md. 13); KPI-007 legal cap arithmetic and RU entities (120 h/yr cap to be confirmed); KPI-008 critical = legal HSE minimum and subcontractor training responsibility; KPI-009 mandatory-competency list; KPI-048 and KPI-050 are board/legal matters; current maternity/paternity leave durations to be verified.

**Definition problems found:** KPI-003 includes annual leave (non-standard; propose a separate "unplanned absence" KPI with types unexcused+sick, 2.5/3.5/5) and its `sick`/`other`/`unpaid` buckets can silently absorb protected leave; KPI-003/028 denominator depends on planned hours being recorded on leave days; KPI-018/021/022 count intended contract/project ends as early turnover; KPI-002 and KPI-019 are period-length dependent and not annualised; neutral KPIs cannot carry band thresholds (KPI-006); thresholds are global with no project/country override; safety rates need a minimum-hours rule and a separate fatality alert; KPI-043 and KPI-031 drafts are needed to make KPI-014A/B and KPI-007 safe and legally meaningful; KPI-009 denominator can be gamed by reclassifying competencies; KPI-020 unknown-category share should feed the quality score.

---

## Ek: Yıllık İzin Sürdürülebilirliği ve İnsani Yaklaşım (2026-11-05)

**Bağlam:** Ürün sahibi kararları (2026-11-05): eşikler uygulanacak; KPI-003 şirket tanımıyla kalır, KPI-053 "Plansız Devamsızlık" (2.5 / 3.5 / 5) ve KPI-054 "Korunan İzin Payı" (Seçenek C) eklenir; mühendislik KPI-055/056'yı grup düzeyinde uygular. Bu ek yalnızca grup düzeyi içindir; hiçbir değer kişi sıralamasında, primde veya disiplinde kullanılmaz.

### E.1 KPI-055 ve KPI-056 eşikleri

| KPI | Yön | Hedef | Uyarı | Kritik | Pencere | Dayanak | Güven | Yan etki / manipülasyon riski |
|---|---|---|---|---|---|---|---|---|
| KPI-055 Yıllık İzin Kullanım Oranı | higher_better ✔ | 90 | 75 | 60 | 12 ay kayan; yıllıklaştırılmaz (pay ve payda aynı dönemde orantılı) | ⚖️ 4857 md. 53 (izinden feragat edilemez), md. 56, Yıllık Ücretli İzin Yönetmeliği; proje işinde mevsimsel erteleme payı için yargı | Orta-düşük (gerçek veri yok) | Proje sonunda / fesihte zorla izin, kâğıt üstünde izin, tek günlük parçalara bölme, iş durması günlerinin izinden düşülmesi |
| KPI-056 Yıllık İzin Kullanmayan Çalışan Oranı | lower_better ✔ | 5 | 10 | 15 | Yalnızca 12 ay | ⚖️ md. 53/56; yasal beklenti ≈ %0, uzun raporlu vb. meşru istisnalar için pay | Orta | Kâğıt üstünde 1 günlük izin yazarak sıfırdan kurtarma, hak doğmadan fesih/sözleşme yenilememe |

**Gerekçe.** Yasal ideal %100 kullanım ve %0 "hiç izin kullanmayan"dır; hedefler bunun altında tutuldu çünkü (i) şantiyede devir eden izin (önceki yıllardan) ve dönemsel kaydırma meşrudur, (ii) KPI-055 >100 olabilir (birikmiş izin eritildiğinde — bu olumludur, eşik sorunu yaratmaz). KPI-055 uyarı 75: ortalama çalışan yıllık hakkının dörtte birinden fazlasını taşıyor demektir; kritik 60: birikim yapısal hale gelmiştir. KPI-056 kritik 15: her yedi uygun çalışandan biri bir yıl boyunca hiç dinlenmemiştir — İSG (yorgunluk, 6331 md. 4) açısından da kırmızıdır.

**12 aylık pencerenin anlamı.**
- Her iki KPI'da eşik kararı **yalnızca 12 aylık kayan pencerede** verilir. Aylık/çeyreklik değerler mevsimseldir (yaz, bayram, kış duruşu); kısa pencerede KPI-055 düşük, KPI-056 yapısal olarak yüksek çıkar. Platform, 12 aydan kısa pencerelerde bu iki KPI için durum rengini göstermemeli ya da "bilgi amaçlı" etiketlemelidir.
- KPI-055 hak payını dönem/365 ile orantılar; bu doğrudur ama izin "topaklı" kullanıldığı için kısa dönem kıyası yanıltıcıdır.
- 12 aylık pencere, dönem sonunda aktif olanları ölçer; dönem içinde ayrılanlar (ve fesihte md. 59 ile parası ödenen izinler) görünmez — **hayatta kalan yanlılığı**. Bu nedenle §E.3'teki "fesihte ödenen kullanılmamış izin günü" izleme göstergesi istenir.

**Manipülasyon ve yan etkiler (kontrol önerisiyle).**
- ⚖️ **Fesih/demobilizasyonda zorla izin:** md. 59/2 gereği bildirim süresi (md. 17) ile yıllık izin iç içe giremez. Proje bitiminde "izne çıkar, sonra çıkış ver" kalıbı oranı iyileştirir ama dinlenme sağlamaz. Kontrol: çıkıştan önceki 30 gün içinde kullanılan izin payı ayrı izlenir.
- ⚖️ **Kâğıt üstünde izin:** izin kaydı + aynı gün puantaj/turnike/fazla mesai kaydı → veri kalitesi uyarısı (grup düzeyinde sayım, kişi listesi yalnızca İK Operasyon yetkisiyle düzeltme için). Bu, hem izin hakkının ihlali hem de ücret/SGK kaydı sorunudur.
- ⚖️ **İzin satın alma / paraya çevirme:** md. 59 kullanılmayan izin ücretinin ödenmesini **yalnızca sözleşmenin sona ermesinde** öngörür; çalışırken izin yerine ödeme yapılması izin borcunu sona erdirmez (Yargıtay yerleşik görüşü — hukuk teyidi). Bordroda "izin ücreti" ödemesi olup izin kaydı olmayan durumlar istisna raporuna düşmelidir.
- ⚖️ **Bölme kuralı (md. 56/2):** izin tarafların anlaşmasıyla bölünebilir, bir bölümü **en az 10 kesintisiz gün** olmalıdır (güncel metinde "en fazla üç bölüm" sınırı da yer alır — hukuk teyidi). Tek günlük parçalarla %90'a ulaşmak dinlenme sağlamaz. Öneri: ileride "12 ayda ≥10 gün kesintisiz izin kullanan uygun çalışan oranı" izleme göstergesi.
- ⚖️ **İş durmasını izinden düşmek:** hava/malzeme kaynaklı duruşta çalışanı tek taraflı izne çıkarmak, md. 40 (zorlayıcı sebeple yarım ücret) ve Yönetmelik'teki çalışanın talebini dikkate alma ilkesini dolanabilir. Kış duruşunda toplu izin ancak önceden ilan edilmiş izin planıyla ve kurul/temsilci görüşüyle meşrudur.
- ⚖️ **Hak doğmadan ayrılık:** 1 yılı doldurmadan sözleşmeyi bitirip yeniden işe alma, KPI-056 paydasını küçültür ve hakkı ortadan kaldırır; md. 54 aynı işverendeki hizmetlerin birleştirilmesini öngörür. KPI-018 (erken ayrılma) ile birlikte okunmalı.
- **Prim:** KPI-055/056 ilk 12 ay **hiçbir primde kullanılmaz**; veri kalitesi (kâğıt izin kontrolü) doğrulandıktan sonra yalnızca proje/şantiye düzeyi yönetim hedefi olabilir, hiçbir zaman kişisel hedef değil.

### E.2 Tanım teyitleri ve düzeltmeler

**KPI-053 Plansız Devamsızlık — TEYİT, bir ek ile.** Tür seti `["unexcused", "sick"]`, eşikler 2.5 / 3.5 / 5, lower_better, payda KPI-003 ile aynı (onaylı puantajdaki planlı saat) — doğru. Yıllık, ücretsiz, diğer mazeret ve `parental` izinler hariç kalır. **Ek (önerilir):** iş kazası ve meslek hastalığı istirahatleri `sick` içinde kaldıkça, KPI-053'ü düşürme baskısı kazaların gizlenmesine dönüşebilir (KPI-014A). Kaynak SGK rapor türünü ayırt edebiliyorsa bunlar ayrı bir türe (ör. `work_injury`) eşlenip KPI-053'ten hariç tutulmalı, KPI-014A bağlamında izlenmelidir (şema değişikliği → Orchestrator/ADR). O zamana kadar KPI-053 yalnızca grup düzeyi ve primsiz kullanılır. ⚖️ 6331 md. 14, 5510 md. 13.

**KPI-054 Korunan İzin Payı — TEYİT, iki sıkılaştırma ile.** Tanım (parental izin saatleri / planlı saat, eşiksiz, neutral, yalnızca grup) doğrudur; `parental` eşlemesi §4 tablosundaki kalemleri (analık raporu, md. 74 ücretsiz izin, süt izni, babalık/evlat edinme izni) kapsamalıdır. Sıkılaştırmalar: (1) bastırma iki koşullu olmalı — izin kullanan kişi sayısı < 5 **veya** grup nüfusu < `HRSWI_MIN_GROUP_SIZE` ise gösterilmez; cinsiyet veya izin alt türü kırılımı ve proje altı (ekip) detayına inme yapılmaz (fark alma yoluyla kimlik çıkarımı riski). (2) Payda: izin günlerinde puantajda planlı saat oluşmuyorsa (özellikle 6 aylık ücretsiz izin) oran düşük çıkar; payda "dönem planlı kapasite saati" olarak doğrulanmalıdır (§5 madde 2 ile aynı sorun). ⚖️ KVKK md. 6 — rol kısıtlı erişim.

**KPI-055/056 — DÜZELTMELER (uygulamadan önce):**
1. **Gün dönüşümü (önemli).** `saat / 9` yalnızca 5 günlük 45 saatlik düzende doğrudur. ⚖️ Yönetmelik'e göre izin günleri **iş günü** olarak sayılır (hafta tatili ve resmî tatiller düşülür, md. 56 son fıkra); 6 günlük şantiye düzeninde gün ≈ 7,5 saattir. 6 günlük düzende tam kullanılan 14 gün (105 saat) `/9` ile 11,7 gün → %83 görünür. Düzeltme: mümkünse **izinli iş günü sayısı** doğrudan sayılsın; değilse bölen kişinin/şantiyenin çalışma düzenine göre (5 gün → 9, 6 gün → 7,5) parametrik olsun. Tek global sabit kabul edilmemeli.
2. **Kıdem.** "Mevcut istihdamdaki kıdem" yerine ⚖️ md. 54'e göre **aynı işverendeki toplam hizmet** (önceki dönemler ve aynı işverenin işyerleri birleştirilerek; deneme süresi dahil; md. 55'teki çalışılmış sayılan süreler dahil) esas alınmalı. Aksi halde proje rotasyonuyla yeniden işe alınan kıdemliler 14 güne veya kapsam dışına düşer.
3. **Hak doğum tarihi.** Dönem sonunda 1 yılı yeni dolduran kişi hakkını kullanmaya fırsat bulamadan KPI-056'yı şişirir. KPI-056 nüfusu: **ilk hak doğum tarihi dönem başında veya öncesinde** olanlar. KPI-055 hak payı: dönem başı ile hak doğum tarihinin geç olanından itibaren orantılansın.
4. **Uzun süreli yokluk.** 12 ayın yarısından fazlasında raporlu, `parental` veya ücretsiz izinde olanlar KPI-056 paydasından çıkarılsın (izin kullanmamaları yönetim sorunu değildir); sayıları yalnızca toplu "hariç tutulan" olarak lineage'da görünür.
5. **Hak kaynağı.** Yasal süre tabandır; TİS/sözleşme daha fazla gün veriyorsa payda sözleşmesel hak olmalı (aksi halde oran yapay biçimde >100 olur). ⚖️ Yer altı işlerinde (md. 53 — yer altı maden işleri için +4 gün) tünel işlerinin kapsama girip girmediği hukukça teyit edilmeli.

### E.3 Sürdürülebilir ve insani yıllık izin yönetimi — politika çerçevesi

1. **Planlama.** Her şantiye/proje, yılbaşından sonraki ilk ay içinde **yıllık izin planı** yapar (⚖️ Yönetmelik: işyerinde izin kurulu veya işveren, çalışanların taleplerini alarak izin çizelgesi hazırlar). Pik sezondan (yaz beton/kaba inşaat, proje teslim dönemleri) **önce** izin rotasyonu planlanır; kış duruşu planlı toplu izin için kullanılabilir ancak yalnızca önceden ilan edilerek.
2. **Ekip kapsaması.** Kritik pozisyonlar (operatör, formen, İSG uzmanı, şef) için yedek/çapraz yetkinlik matrisi tutulur; "yerine bakacak kimse yok" izin reddi gerekçesi olamaz — bu bir kadro planlama açığıdır ve KPI-009 / işe alım planına girer. İzin talebinin reddi yazılı gerekçe ve alternatif tarih ile verilir.
3. **Cezalandırmama.** Yıllık izin hiçbir KPI'da, primde, performans değerlendirmesinde, puantaj teşvikinde ("devam primi") olumsuz etki yaratmaz. KPI-003'teki yıllık izin payı yorumlanırken KPI-053 esas alınır: KPI-003 kırmızı + KPI-053 yeşil = planlı izin, sorun değil. ⚖️ Devam primlerinin yıllık izni devamsızlık sayması hukuka aykırılık riski taşır.
4. **Yönetici sorumluluğu.** Şantiye/proje müdürü: izin planının yapılması, yılda en az bir ≥10 günlük kesintisiz izin fırsatı, birikmiş izinlerin (ör. >1 yıllık hak) eritme planı. İK: plan uyumunu ve KPI-055/056'yı izler, eğitim verir. Değerlendirme organizasyon düzeyindedir.
5. **KPI-056 ile erken uyarı.** Ekip/şantiye düzeyinde, yalnızca toplu oran olarak; nüfus `HRSWI_MIN_GROUP_SIZE` altındaysa bastırılır. Çeyrek sonunda "son 9 ayda izin kullanmayan uygun çalışan oranı" eşiği aşan birimler için yöneticiye **plan hatırlatması** gider. Kişi listeleri gösterge panosunda yer almaz; izin planlaması için gereken isimli bilgi yalnızca kendi ekibinin izin bakiyesini gören yöneticide ve İK operasyonda (mevcut RBAC/ABAC) kalır. Ekipler arası sıralama tablosu (league table) yapılmaz.
6. **⚖️ Devir ve zaman aşımı.** Yıllık izin hakkı iş ilişkisi sürdükçe **sona ermez ve paraya çevrilemez**; kullanılmayan izin sonraki yıla devreder. Ücreti yalnızca fesihte, son ücret üzerinden ödenir (md. 59) ve zaman aşımı sözleşmenin sona ermesiyle başlar (5 yıl). Şirket içi "kullan ya da kaybet" kuralı **uygulanamaz**. Birikim aynı zamanda finansal yükümlülüktür (TMS 19 izin karşılığı) — Finans ile paylaşılmalı.
7. **Yurt dışı şantiyeler.** Yerel hukuk ve (varsa) Türk hukukunun uygulanması sözleşmeye bağlıdır ⚖️; daha lehe olan uygulanır. Rotasyon düzeni (ör. 10–12 hafta saha / 2–3 hafta ev) yıllık izinle **karıştırılmamalı**; rotasyon izni yıllık izin hakkını tüketiyorsa bu açıkça sözleşmede yazmalı ve hukukça onaylanmalı. ⚖️ md. 56: izni başka yerde geçirecek çalışana talep halinde toplam 4 güne kadar ücretsiz yol izni verilir; yurt dışı/uzak şantiyede yol süresinin ücretli sayılması önerilir (insani yaklaşım). Ülke bazlı ayrı eşik/okuma gerekir (§5'teki ülke override eksikliği).
8. **Alt yükleniciler.** Alt yüklenici işçilerinin izin hakkı kendi işverenlerine aittir, ancak ⚖️ 4857 md. 2/6 gereği asıl işveren alt işverenin işçilere karşı yükümlülüklerinden **müteselsilen sorumludur**. Alt yüklenici sözleşmelerine izin planı ve yıllık bildirim (toplu KPI-055/056) şartı konmalı; hak edişte izin ücreti/fesih ödemelerinin kanıtı istenmeli. Platformda alt yüklenici bazında yalnızca toplu oran.
9. **Platform desteği (yalnızca grup düzeyi).**
   - **Pano "İzin Sağlığı":** proje/şantiye/departman/alt yüklenici kırılımında KPI-055, KPI-056 (12 ay), KPI-053 ve KPI-003 yan yana; birikmiş izin günü dağılımı (bantlar: 0–14, 15–28, >28 gün, kişi sayısı olarak, bastırmalı).
   - **Uyarılar:** KPI-056 uyarı/kritik eşiği (12 ay); kâğıt izin veri kalitesi uyarısı (izin + çalışma kaydı çakışması); bordroda izin ödemesi var / fesih yok istisnası; çıkış öncesi 30 günde yoğun izin örüntüsü.
   - **İzleme göstergeleri (eşiksiz, ileride):** ≥10 gün kesintisiz izin kullanan oranı; fesihte ödenen kullanılmamış izin günü / çıkan kişi.
   - Kişi sıralaması, kişi bazlı skor veya ekip lig tablosu **yok**.

### E.4 Gerçek organizasyondan gerekenler (bu ek için)

Kişi bazında çalışma düzeni (5/6 gün, günlük saat) ve şantiye takvimi; aynı işverendeki önceki hizmetler (md. 54 birleştirme); TİS/sözleşmesel izin hakları; mevcut izin bakiyeleri (açılış bakiyesi); yurt dışı sözleşmelerinde uygulanacak hukuk ve rotasyon/izin ilişkisi; alt yüklenicilerden toplu izin verisi; SGK rapor türü ayrımı (iş kazası / hastalık / analık).

### E.5 Kısa özet (Orchestrator için)

- **KPI-055** (higher_better): **90 / 75 / 60**, 12 ay kayan, yıllıklaştırma yok, güven orta-düşük.
- **KPI-056** (lower_better): **5 / 10 / 15**, yalnızca 12 ay, güven orta.
- **KPI-053:** teyit (unexcused + sick, 2.5 / 3.5 / 5); önerilen ek: iş kazası/meslek hastalığı istirahatini ayrı türe alıp hariç tutmak (ADR).
- **KPI-054:** teyit; bastırma = izin kullanan < 5 **veya** grup < `HRSWI_MIN_GROUP_SIZE`; alt kırılım yok; payda doğrulaması.
- **KPI-055/056 düzeltmeleri:** (1) `/9` yerine izinli iş günü sayımı veya çalışma düzenine göre bölen (6 gün → 7,5); (2) kıdem = aynı işverendeki toplam hizmet (md. 54); (3) KPI-056 nüfusu = hak doğum tarihi dönem başında/öncesinde olanlar, KPI-055 hak payı hak doğumundan itibaren; (4) dönemin yarısından fazlası uzun yoklukta olanlar KPI-056 paydasından hariç; (5) payda sözleşmesel hak (yasal taban).
- **⚖️ Hukuk onayı:** md. 56/2 bölme kuralının güncel metni; çalışırken ödenen izin ücretinin izin borcunu sona erdirmediği; md. 59/2 bildirim–izin çakışması; tünel işlerinde +4 gün; yurt dışı rotasyon ile yıllık izin ilişkisi; alt işveren müteselsil sorumluluğu; devam priminin yıllık izni devamsızlık sayması.
