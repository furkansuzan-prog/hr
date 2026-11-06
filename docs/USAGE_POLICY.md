# HRSWI Kullanım Politikası

Karar: **D-14 (PO-7)**, 2026-11-06 — `docs/DECISIONS.md`. Sahibi: İK Direktörü (Agent 17); uygulama: Agent 07/09/14/15.

## 1. Amaç

HRSWI çıktıları (KPI'lar, paneller, tahminler, anomali uyarıları, raporlar ve yapay zekâ asistanı yanıtları) **toplu, grup düzeyinde karar desteğidir**. Kurumun iş gücü sürdürülebilirliğini, verimliliğini ve iş sağlığı ve güvenliği durumunu izlemek ve iyileştirme önceliklerini belirlemek için kullanılır.

## 2. Yasak kullanımlar

Platform çıktıları aşağıdaki işlemlerin **hiçbirinde** girdi, gerekçe veya ölçüt olarak kullanılamaz:

- ücret, ücret artışı veya yan hak belirleme;
- prim, ikramiye veya teşvik hesaplama;
- terfi, görev değişikliği veya kadro kararı;
- disiplin işlemi, uyarı, fesih veya işten çıkarma;
- bireysel performans değerlendirmesi, bireysel sıralama veya bireysel risk puanı.

Bir ekip, proje veya firma göstergesi (ör. devamsızlık, fazla mesai, yıllık izin kullanmama, kaza sıklık oranı) **o gruptaki kişiler hakkında bireysel sonuç çıkarmak için kullanılmaz**. Göstergeler süreç ve koşulların iyileştirilmesine yöneliktir (ör. KPI-056 yalnızca ekip düzeyinde iş yükü erken uyarısıdır, D-7).

## 3. Platformdaki teknik güvenceler (mevcut davranış)

| Güvence | Nerede |
|---|---|
| Küçük örneklem bastırma: `HRSWI_MIN_GROUP_SIZE` (varsayılan 5) altındaki gruplarda değer gösterilmez; sağlık kaynaklı izin türleri kırılımda maskelenir | `backend/app/analytics/engine.py`, `calculators.py` |
| Yapay zekâ asistanı işten çıkarma, ücret, prim, terfi, disiplin ve bireysel risk puanı isteklerini **reddeder** (TR/EN/RU) ve insan incelemesine yönlendirir | `backend/app/ai/assistant.py` (`PROHIBITED`, `refuse`), test `test_assistant_refuses_individual_decisions` |
| İSG kayıtları kimlik, tanı ve serbest metin içermez; İSG yalnızca kendi personelini kapsar (D-10). Sağlık kaynaklı KPI'lar için ayrı `health.read` yetkisi (D-12) ayrı iş paketinde uygulanıyor | `docs/SECURITY_PRIVACY.md`, ADR-021 |
| Her rapor (JSON, XLSX, CSV, PDF) ve panel bu politikanın kısa uyarısını taşır | `backend/app/services/reports.py` (`USAGE_NOTICE`), `frontend/src/components/usage-notice.tsx` |
| Rapor görüntüleme ve dışa aktarma denetim izine yazılır (`report.view`, `report.export`) | `audit_logs`, `backend/app/api/routers/analytics.py` |

## 4. KVKK — amaçla sınırlılık

6698 sayılı KVKK md. 4/2 (b, c) uyarınca kişisel veriler belirli, açık ve meşru amaçlarla işlenir ve bu amaçla bağlantılı, sınırlı ve ölçülü olmalıdır. HRSWI'ye aktarılan veriler **toplu iş gücü analitiği** amacıyla işlenir; bu verilerin yukarıdaki bireysel kararlarda kullanılması amaç dışı işleme sayılır. Bireysel bir karar gerekiyorsa, kaynak sistemdeki (bordro, PDKS, İK dosyası) veriler ilgili mevzuat ve kurum prosedürü çerçevesinde, insan değerlendirmesiyle kullanılır.

## 5. Uyarı metni

Arayüzde (yönetim paneli ve raporlar sayfası) ve üretilen her raporda görünen kısa metin:

> **Kullanım politikası:** Bu çıktı toplu (grup düzeyi) karar desteğidir; ücret, prim, terfi, disiplin veya bireysel performans değerlendirmesinde kullanılamaz.

İngilizce ve Rusça karşılıkları `reports.USAGE_NOTICE` ve `frontend/src/lib/i18n.tsx` (`usagePolicy.notice`) içindedir; metin değişikliği iki yerde birlikte yapılır.

## 6. İhlal ve soru

Politikaya aykırı bir kullanım fark edildiğinde İK Direktörü ve KVKK irtibat kişisine bildirilir. Bu belge hukuki görüş değildir; hukuki değerlendirme gerektiren sorular `docs/hr/legal/` üzerinden insan hukukçuya iletilir.
