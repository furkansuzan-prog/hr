# HRSWI — Ürün Kararları Kaydı

Ürün sahibinin verdiği ve sistem davranışını belirleyen kararlar. Teknik kararlar `ARCHITECTURE_DECISIONS.md`'de.

| # | Tarih | Karar | Uygulama | Test |
|---|---|---|---|---|
| D-1 | 2026-11-05 | Alt yüklenici verisi, paylaşım izni verildiğinde **geçmiş dönemleriyle birlikte** görüntülenebilir | Mevcut davranış korundu (`security/access.py`: bugünkü geçerli ilişki + `data_sharing_granted`) | `test_hierarchy_change_over_time`, `test_main_contractor_sees_only_shared_subcontractors` |
| D-2 | 2026-11-05 | **Dönem kapatma/açma yetkisi ana yüklenicide** | `engine.can_close_periods`: tenant geneli veya kapsamında `main_contractor` bulunan `period.close` sahibi; yalnızca alt yükleniciye kapsamlı kullanıcı 403 (denetim kaydı). Kapanışta tüm kapsamlar (tenant + her firma, maliyet dahil) sistem çalıştırması olarak dondurulur; kapatan kişiye yalnızca sayı döner | `test_period_authority.py`, `test_main_contractor_close_freezes_every_scope_including_cost` |
| D-3 | 2026-11-05 | **Devamsızlık (KPI-003)** = habersiz devamsızlık + raporlu gün + yıllık izin + ücretsiz izin + diğer izinler. Resmî tatil hariç; doğum/ebeveyn izni İK onayına kadar hariç | KPI-003 yeni formül versiyonu (mevcut DB'lerde katalog senkronizasyonu v2 ekler; eski sonuçlar v1 ile korunur). Eski %4/%6 eşikleri kaldırıldı | `test_absenteeism_and_overtime_exact` (6.0 %), `test_kpi028_sickness_and_kpi003_unchanged` (7.0 %), `test_formula_change_preserves_old_results` |
| D-4 | 2026-11-05 | **KPI hedef ve eşikleri İK yöneticisi belirleyecek** | Doldurulabilir form: `docs/KPI_TARGETS_REQUEST.md` (koddan ve demo veriden üretilir: `backend/scripts/kpi_targets_form.py`) | — (İK girdisi bekleniyor) |
