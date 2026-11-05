# HRSWI — Güvenlik, Gizlilik ve Uyum · Agent 11

> **Önemli:** Bu belge teknik kontrolleri açıklar. KVKK/GDPR uyumu yalnızca teknik özelliklerle sağlanmış sayılmaz; aydınlatma metinleri, hukuki dayanaklar, VERBİS kaydı, veri işleme envanteri, yurt dışı aktarım ve alt işleyen sözleşmeleri gibi **hukuki ve organizasyonel kontroller ayrıca** yürütülmelidir. ⚖️ işaretli maddeler hukuki inceleme gerektirir.

## 1. Uygulanan teknik kontroller (kodda doğrulanabilir)
| Kontrol | Uygulama | Kanıt (test) |
|---|---|---|
| Kimlik doğrulama | scrypt parola özeti, 60 dk JWT, imza/issuer/süre doğrulama; prod'da varsayılan sır ile açılmaz | `test_security.py::test_tampered_token_rejected`, `test_production_config_refuses_default_secret` |
| Yetkilendirme (RBAC) | 25 izin, 15 rol şablonu; rol = veri | `docs/PERMISSION_MATRIX.md` |
| Kayıt düzeyi (ABAC) | İzin başına tüzel kişilik kapsamı; alt yüklenici yalnızca geçerli + paylaşım izinli ilişkiyle | `test_subcontractor_isolation`, `test_main_contractor_sees_only_shared_subcontractors`, `test_hierarchy_change_over_time` |
| Alan düzeyi | Maliyet KPI'ları `cost.read` gerektirir; isim/sicil `people.pii` olmadan maskelenir; başka işverendeki istihdam dönemleri gizlenir | `test_cost_kpi_requires_cost_permission`, `test_pii_masked_without_permission`, `test_person_history_hides_other_employer_spells` |
| Veri minimizasyonu | Doğum yılı (tarih değil); TC kimlik no yalnızca HMAC-SHA256; sağlık tanısı ve olaya karışan kişi kimliği tutulmaz | `test_no_raw_national_id_stored` |
| Küçük örneklem bastırma | n < 5 kişi → değer/pay/payda gizli; anket n < 10 saklanmaz (DB CHECK) | `test_small_group_suppressed`, `ck_survey_min_group` |
| Denetim izi | Giriş (başarılı/başarısız), yetki reddi, kişi listeleme/geçmiş, rapor görüntüleme/indirme, import, KPI çalıştırma, formül versiyonu, dönem kapatma/açma | `test_unauthorised_report_download_denied_and_audited`, `test_permission_denial_is_audited` |
| Güvenli dışa aktarma | XLSX/PDF/CSV yalnızca `report.export`; meta veride filtre ve kullanıcı | `test_reports_all_formats` |
| Yapay zekâ sınırları | DB'ye doğrudan erişim yok; kullanıcı kapsamıyla servis çağrısı; bireysel karar talepleri reddedilir; LLM varsayılan kapalı, kişisel veri gönderilmez | `test_assistant_refuses_individual_decisions`, `test_assistant_respects_scope` |
| Loglar | Yapılandırılmış JSON; yalnızca kimlikler (request/user/tenant), kişisel veri yok | `app/core/logging.py` |
| HTTP | CORS beyaz liste, `nosniff`, `no-store`, istek kimliği | `app/main.py` |
| Test verisi | Yalnızca sentetik üretici; gerçek kişisel veri yok | `app/seed/synthetic.py` |

## 2. Bilinen boşluklar (yayın öncesi kapatılmalı)
| # | Boşluk | Plan |
|---|---|---|
| G1 | OIDC/MFA yok (yerel parola) | FAZ 7: OIDC Authorization Code + PKCE, MFA IdP'de zorunlu |
| G2 | JWT tarayıcıda `localStorage` (XSS riski) | BFF + httpOnly/SameSite çerez |
| G3 | PostgreSQL RLS yok (yetki uygulama katmanında) | `app.current_tenant` GUC + RLS politikaları |
| G4 | Saklama (at-rest) şifreleme ve anahtar yönetimi altyapıya bırakıldı | Disk/DB şifreleme + KMS; alan bazlı şifreleme (ücret) değerlendirilecek |
| G5 | Saklama–imha işleri otomatik değil | Politika tablosu + zamanlanmış anonimleştirme |
| G6 | Brute-force koruması / rate limit yok | Reverse proxy + hesap kilitleme |
| G7 | KVKK md.11 başvuru süreci (erişim/düzeltme/silme) için iş akışı yok | Talep kaydı + veri dışa aktarma/anonimleştirme |
| G8 | Formül versiyonları global (tenant'a özel devamsızlık tanımı yok) | Tenant düzeyinde formül override (P1) |

## 3. Saklama ve imha politikası (öneri ⚖️)
| Veri | Öneri | Dayanak (incelenmeli) |
|---|---|---|
| Özlük / istihdam | İlişki bitiminden sonra 10 yıl, sonra anonimleştirme | 4857 s. Kanun, TBK zamanaşımı |
| Puantaj, ücret | 10 yıl | Vergi/SGK mevzuatı |
| İSG olay kayıtları | 15 yıl (toplu, kişisiz) | 6331 s. Kanun |
| Denetim logları | 2 yıl sıcak + 5 yıl arşiv | Kurum politikası |
| Import ham satırları (`import_staging_rows.raw`) | Commit'ten sonra 90 gün | Veri minimizasyonu — ⚠️ ham satırlar şu an süresiz tutuluyor |
| Anket | Yalnızca toplu sonuç; bireysel yanıt tutulmaz | KVKK md.4 |

## 4. İhlal müdahale prosedürü (özet)
1. Tespit → olay kaydı (zaman, kapsam, etkilenen tenant/veri kategorisi).
2. Sınırlama: ilgili token/kullanıcı devre dışı, sır rotasyonu (`HRSWI_JWT_SECRET` değişince tüm oturumlar düşer).
3. Denetim logundan etki analizi (`audit_logs` kullanıcı/aksiyon/zaman).
4. ⚖️ KVKK Kurulu'na 72 saat içinde bildirim değerlendirmesi; ilgili kişilere bildirim.
5. Kök neden, düzeltici aksiyon (Modül M kaydı), tatbikat.

## 5. Hukuki inceleme gerektiren konular ⚖️
- Fazla mesai yıllık üst sınırı (270 saat, 4857 md.41) ve gece çalışması tanımı (md.69) KPI eşiklerinde varsayılan parametredir; ülke/tenant bazında doğrulanmalı.
- Devamsızlık tanımına hangi izin türlerinin girdiği (KPI-003 parametresi) kurum politikasıdır.
- Alt yüklenici çalışanı verisinin ana yükleniciye aktarımının hukuki dayanağı (`data_sharing_granted` yalnızca teknik bayraktır; sözleşme kanıtı ayrıca tutulmalı).
- Kart okuma verisi yalnızca varlık kanıtıdır; ücret hak edişi veya üretken süre kanıtı olarak kullanılmaz (Modül D).
- Yurt dışı (RU tüzel kişilikleri) veri aktarımı ve yerelleştirme gereksinimleri.
- ISO 30414:2025, ESRS (Omnibus sonrası), GRI ve TSRS sürüm ve uygulanabilirliği — sistemde `version_verified=false`.
