# HRSWI — Ürün Gereksinimleri (PRD) · Agent 01

## 1. Problem ve hedef
Büyük proje/inşaat organizasyonlarında iş gücü verisi (ana ve alt yükleniciler, puantaj, maliyet, İSG, eğitim) dağınık, tutarsız ve tarihsel olarak kayıplıdır. HRSWI; veriyi toplar, doğrular, **versiyonlu ve izlenebilir** KPI'lara dönüştürür, tahmin ve karar desteği sağlar ve aksiyonların sonucunu ölçer.

## 2. Personalar
| Persona | Rol (sistem) | Temel ihtiyaç |
|---|---|---|
| Üst yönetim | READONLY_EXECUTIVE | Tek ekranda iş gücü, maliyet, risk ve tahmin |
| İK direktörü / müdürü | HR_DIRECTOR / HR_MANAGER | Devir, devamsızlık, kadro, eğitim; dönem kapanışı |
| Proje / operasyon yöneticisi | PROJECT_MANAGER / OPERATIONS_DIRECTOR | Plan–gerçekleşen, verimlilik, yüklenici karşılaştırma |
| Saha sorumlusu | DEPARTMENT_MANAGER | Ekip kapasitesi, fazla mesai, yetkinlik |
| Alt yüklenici yöneticisi | CONTRACTOR_MANAGER (kendi firması) | Kendi personel verisini yükleme ve izleme |
| Sürdürülebilirlik müdürü | SUSTAINABILITY_MANAGER | Standart bazlı veri kapsamı, kanıt |
| Finans analisti | FINANCE_ANALYST | Maliyet, bütçe, birim maliyet |
| İç denetçi | INTERNAL_AUDITOR | Denetim izi, formül geçmişi |

## 3. Kullanıcı hikâyeleri (MVP = FAZ 1–3; ✔ bu sürümde uygulandı ve test edildi)
| ID | Hikâye | Kabul kriteri | Durum |
|---|---|---|---|
| US-01 | Güvenli oturum açabilmeliyim | Yanlış parola 401 + denetim kaydı; süresi geçmiş/değiştirilmiş token 401 | ✔ |
| US-02 | Firma–proje–yüklenici hiyerarşisini görebilmeliyim | Tarihe göre geçerli ilişkiler; paylaşım izni yoksa ana yüklenici alt firmayı görmez | ✔ |
| US-03 | Personel verisini Excel/CSV ile aktarabilmeliyim | Önizleme, alan eşleştirme, satır bazlı hata raporu, mükerrer dosya reddi, kaldığı yerden devam | ✔ |
| US-04 | Geçmiş işveren değişiklikleri kaybolmamalı | Aynı kişinin çoklu istihdam dönemi; yeni işveren yeni kayıt | ✔ |
| US-05 | KPI'ları güvenilir hesaplamalıyım | El hesabıyla birebir test edilmiş 18 aktif KPI; payda 0 → hesaplanamaz | ✔ |
| US-06 | Dashboard gerçek backend verisiyle çalışmalı | Tüm sayılar API'den; demo veri etiketi | ✔ |
| US-07 | Dönem/firma/proje filtreleri her yerde aynı çalışmalı | Tablo, grafik, tekil değer aynı sonucu verir (test) | ✔ |
| US-08 | Yetkisiz erişim engellenmeli | Alt yüklenici izolasyonu, maliyet alan koruması, PII maskeleme, denetim izi | ✔ |
| US-09 | Eksik veri açıkça görülmeli | `insufficient_data` / `suppressed` durumları ve veri kalite puanı ekranda | ✔ |
| US-10 | Yönetim raporu üretebilmeliyim | Aylık İK raporu ve alt yüklenici raporu: XLSX/PDF/CSV + meta veri | ✔ |
| US-11 | Formül değişince eski sonuçlar korunmalı | Versiyon 1 sonucu, v2 sonrası aynen okunur | ✔ |
| US-12 | Kapanmış dönem kontrolsüz değişmemeli | Kapalı dönemde yeniden hesap 409; gerekçeli yeniden açma | ✔ |
| US-13 | Önümüzdeki 3 ayın personel ihtiyacını görmeliyim | Belirsizlik aralıklı tahmin; yetersiz veri reddi; tahmin etiketi | ✔ |
| US-14 | Doğal dilde soru sorabilmeliyim | TR/EN/RU; sayılar KPI motorundan; bireysel karar talepleri reddedilir; kapsam uygulanır | ✔ (deterministik yönlendirici; LLM FAZ 6) |
| US-15 | Riskleri ve aksiyonları izleyebilmeliyim | Başlangıç/hedef/sonuç değeri, etkinlik | ✔ okuma · ✖ oluşturma/düzenleme UI (FAZ 4) |
| US-16 | Standart bazlı veri kapsamını görmeliyim | ISO 30414, GRI, ESRS S1/S2, TSRS, ISO 45001 kapsam göstergesi + "uyum iddiası değildir" | ✔ (versiyonlar doğrulanmadı) |

## 4. MVP dışı (sonraki sürümler)
Kadro planı ve senaryo simülasyonu UI (Modül I), vardiya planlama, anket toplama aracı (şu an yalnızca toplu sonuç), çevresel etki (Modül N, KPI-052 taslak), dinamik rapor oluşturucu, planlanmış raporlar, OIDC/MFA, PostgreSQL RLS, Celery işleri, LLM entegrasyonu, ERP/bordro/SGK bağlayıcıları.

## 5. Fonksiyonel olmayan gereksinimler
| Alan | Hedef | Bu sürümde ölçülen |
|---|---|---|
| Ölçek | 15K aktif / 100K+ tarihsel kişi, 200+ firma | Demo: 2.5K kişi, 14 firma. Ölçek testi **yapılmadı** (bkz. PERFORMANCE_REPORT) |
| Performans | Dashboard p95 < 2 sn | 30 eşzamanlı istek, sıcak önbellek: p95 1.06 sn ✔ · 150 eşzamanlı (bekleme süresiz): p95 3.6 sn ✖ |
| Güvenlik | KVKK, RBAC+ABAC, denetim izi | Uygulandı; hukuki inceleme gerektiren maddeler `SECURITY_PRIVACY.md` |
| Dil | TR/EN/RU | Arayüz çekirdeği + KPI adları + asistan; tam çeviri FAZ 7 |
| Erişilebilirlik | WCAG 2.1 AA hedefi | Klavye odağı, aria etiketleri, skip-link; otomatik a11y denetimi yapılmadı |
