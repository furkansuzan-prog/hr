# HRSWI — Güvenlik, Gizlilik ve Uyum · Agent 11

> **Önemli:** Bu belge teknik kontrolleri açıklar. KVKK/GDPR uyumu yalnızca teknik özelliklerle sağlanmış sayılmaz; aydınlatma metinleri, hukuki dayanaklar, VERBİS kaydı, veri işleme envanteri, yurt dışı aktarım ve alt işleyen sözleşmeleri gibi **hukuki ve organizasyonel kontroller ayrıca** yürütülmelidir. ⚖️ işaretli maddeler hukuki inceleme gerektirir.

## 1. Uygulanan teknik kontroller (kodda doğrulanabilir)
| Kontrol | Uygulama | Kanıt (test) |
|---|---|---|
| Kimlik doğrulama | scrypt parola özeti, 60 dk JWT, imza/issuer/süre doğrulama; prod'da varsayılan sır ile açılmaz | `test_security.py::test_tampered_token_rejected`, `test_production_config_refuses_default_secret` |
| Brute-force koruması (G6) | Hesap kilitleme: art arda 5 hatalı parola → 15 dk kilit (`users.failed_login_count`, `users.locked_until`), başarılı girişte sayaç sıfırlanır, kilit süresince yapılan denemeler kilidi uzatmaz. IP başına kayan pencere: 5 dk'da 20 **başarısız** deneme → `429` + `Retry-After` (Redis varsa çok işçili, yoksa işlem içi). Ayarlar: `HRSWI_LOGIN_LOCKOUT_THRESHOLD`, `HRSWI_LOGIN_LOCKOUT_MINUTES`, `HRSWI_LOGIN_IP_MAX_FAILURES`, `HRSWI_LOGIN_IP_WINDOW_SECONDS`, `HRSWI_LOGIN_PROTECTION_ENABLED`. Denetim: `auth.login_failed`, `auth.locked`, `auth.rate_limited` | `test_security_hardening.py::test_lockout_after_threshold_and_unlock_after_expiry`, `test_successful_login_resets_failure_counter`, `test_ip_rate_limit_returns_429_with_retry_after`, `test_successful_logins_do_not_consume_ip_budget` |
| Kullanıcı numaralandırma engeli | Bilinmeyen e-posta, yanlış parola, pasif hesap ve **kilitli hesap** aynı `401 {"detail":"Invalid credentials"}` yanıtını alır (kilit 429 ile bildirilmez; aksi halde kilit, hesabın varlığını ele verirdi). Her yolda tam bir scrypt çalışır (zamanlama karşılaştırılabilir). 429 yalnızca IP'ye bağlıdır | `test_unknown_email_and_wrong_password_are_indistinguishable` |
| Token iptali | JWT `tv` claim'i = `users.token_version` (zorunlu claim). `POST /api/v1/auth/logout-all` sürümü artırır → kullanıcının tüm cihazlardaki tokenları geçersiz; `tv`'siz veya eski `tv`'li token 401. Pasif kullanıcı (`is_active=false`) zaten reddedilir. Denetim: `auth.logout_all` | `test_logout_all_revokes_old_tokens_but_new_login_works`, `test_token_without_or_with_stale_tv_rejected`, `test_deactivated_user_token_rejected` |
| Saklama–imha (import çalışma verisi, B-03) | `app/services/retention.py`: `committed`/`failed` olup 90 günden eski batch'lerin `import_staging_rows.raw` → `{}`, `mapped` yalnızca kişisel olmayan anahtarlar (`legal_entity_code`, `start_date`, `end_date`, `contract_type`, `termination_category`, `project_code`, `occupation_code`, `source_system`); süresi dolan `failed` batch'ler `expired` olur (yarım satırla yeniden commit engellenir). 90 günden eski `merged`/`rejected` kimlik inceleme kayıtlarının `payload`'u temizlenir. Analitik/ana tablolara dokunulmaz. Varsayılan kuru çalıştırma; `--apply` her çalıştırmada tenant başına + özet `retention.purge` denetim kaydı yazar. CLI: `python -m app.services.retention [--apply] [--staging-days N] [--review-days N]` | `test_retention_dry_run_then_apply`, `test_retention_rejects_nonsense_periods`, `test_retention_cli_dry_run` |
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
| G10 | Token iptali kullanıcı bazında ve "hepsini" iptal eder; tek oturum (jti) iptali, parola değişiminde otomatik iptal ve rol değişiminde iptal yok (rol değişikliği zaten her istekte DB'den okunur). Her istekte `users` satırı okunur (mevcut davranış) | jti denylist (Redis) + parola değişiminde `token_version` artırımı |
| G5 | **Kısmen kapandı:** import çalışma verisi için imha işi var (B-03, yukarıda) ama **zamanlayıcıya bağlı değil** (cron/K8s CronJob DevOps'ta); özlük/puantaj/denetim logu saklama süreleri (bölüm 3) için politika tablosu ve anonimleştirme yok; `data_quality_issues.record_ref` (dış kimlik) ve `message` temizlenmiyor | Günlük `python -m app.services.retention --apply` zamanlaması; politika tablosu + zamanlanmış anonimleştirme |
| G6 | **Uygulandı** (bölüm 1). Kalan riskler: (a) hesap kilitleme, e-postayı bilen saldırganın hesabı 15 dk'lık aralıklarla kilitli tutmasına izin verir (DoS) — IP limiti ve kilidin uzatılmaması ile sınırlandı; (b) IP penceresi Redis yoksa işçi başınadır; (c) ters proxy arkasında `--proxy-headers`/`--forwarded-allow-ips` ayarlanmazsa tüm istemciler proxy IP'si görünür; (d) dağıtık (çok IP'li) parola püskürtme yalnızca hesap kilidiyle sınırlanır; CAPTCHA/MFA yok (G1) | Edge/WAF rate limit, OIDC + MFA (G1) |
| G7 | KVKK md.11 başvuru süreci (erişim/düzeltme/silme) için iş akışı yok | Talep kaydı + veri dışa aktarma/anonimleştirme |
| G8 | Formül versiyonları global (tenant'a özel devamsızlık tanımı yok); değişiklik `admin.platform` ister, DB tetikleyicisiyle değişmez | Tenant düzeyinde formül override (P1) |
| G9 | ✔ **Karar verildi (2026-11-05):** alt yüklenici görünürlüğü bugünkü paylaşım iznine göre — izin verildiğinde firmanın geçmiş verisi de görünür (bilinçli kabul). ⚖️ Hukuki dayanak: paylaşım sözleşmesi geçmiş dönemleri kapsamalı | Sözleşme kanıtının kayıt altına alınması (organizasyonel kontrol) |

## 3. Saklama ve imha politikası (öneri ⚖️)
| Veri | Öneri | Dayanak (incelenmeli) |
|---|---|---|
| Özlük / istihdam | İlişki bitiminden sonra 10 yıl, sonra anonimleştirme | 4857 s. Kanun, TBK zamanaşımı |
| Puantaj, ücret | 10 yıl | Vergi/SGK mevzuatı |
| İSG olay kayıtları | 15 yıl (toplu, kişisiz) | 6331 s. Kanun |
| Denetim logları | 2 yıl sıcak + 5 yıl arşiv | Kurum politikası |
| Import ham satırları (`import_staging_rows.raw`, `mapped`'deki kişisel alanlar) | Commit/başarısızlıktan sonra 90 gün | Veri minimizasyonu — kimlik no saklamadan önce HMAC'lenir; imha işi `app/services/retention.py` (zamanlanması gerekir, G5) |
| Kimlik inceleme kuyruğu (`identity_review_items.payload`) | Karar (`merged`/`rejected`) sonrası 90 gün | Veri minimizasyonu |
| Anket | Yalnızca toplu sonuç; bireysel yanıt tutulmaz | KVKK md.4 |

## 4. İhlal müdahale prosedürü (özet)
1. Tespit → olay kaydı (zaman, kapsam, etkilenen tenant/veri kategorisi).
2. Sınırlama: kullanıcının tüm tokenları `POST /api/v1/auth/logout-all` (veya `users.token_version` artırımı) ile iptal, kullanıcı devre dışı, sır rotasyonu (`HRSWI_JWT_SECRET` değişince tüm oturumlar düşer).
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
