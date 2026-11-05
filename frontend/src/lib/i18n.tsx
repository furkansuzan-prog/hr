"use client";
import { createContext, useContext, useEffect, type ReactNode } from "react";
import { usePersisted } from "./store";

export type Locale = "tr" | "en" | "ru";

const dict = {
  "nav.overview": { tr: "Genel Bakış", en: "Overview", ru: "Обзор" },
  "nav.organization": { tr: "Organizasyon", en: "Organisation", ru: "Организация" },
  "nav.people": { tr: "Personel Analitiği", en: "Workforce Analytics", ru: "Аналитика персонала" },
  "nav.sustainability": { tr: "Sürdürülebilirlik", en: "Sustainability", ru: "Устойчивость" },
  "nav.productivity": { tr: "Verimlilik", en: "Productivity", ru: "Производительность" },
  "nav.planning": { tr: "İş Gücü Planlama", en: "Workforce Planning", ru: "Планирование" },
  "nav.contractors": { tr: "Alt Yükleniciler", en: "Subcontractors", ru: "Субподрядчики" },
  "nav.hse": { tr: "İş Sağlığı ve Güvenliği", en: "Health & Safety", ru: "Охрана труда" },
  "nav.training": { tr: "Eğitim ve Yetkinlik", en: "Training & Competency", ru: "Обучение" },
  "nav.cost": { tr: "Maliyet Yönetimi", en: "Cost Management", ru: "Затраты" },
  "nav.ai": { tr: "Yapay Zekâ Analiz Merkezi", en: "AI Analysis Centre", ru: "Центр ИИ-анализа" },
  "nav.risks": { tr: "Risk ve Aksiyonlar", en: "Risks & Actions", ru: "Риски и меры" },
  "nav.reports": { tr: "Raporlama", en: "Reporting", ru: "Отчёты" },
  "nav.integrations": { tr: "Veri Entegrasyonları", en: "Data Integrations", ru: "Интеграции" },
  "nav.admin": { tr: "Sistem Yönetimi", en: "Administration", ru: "Администрирование" },
  "nav.kpis": { tr: "KPI Kataloğu", en: "KPI Catalogue", ru: "Каталог KPI" },
  "common.planned": { tr: "Planlandı", en: "Planned", ru: "Запланировано" },
  "common.logout": { tr: "Çıkış", en: "Log out", ru: "Выйти" },
  "common.period": { tr: "Dönem", en: "Period", ru: "Период" },
  "common.entity": { tr: "Firma", en: "Company", ru: "Компания" },
  "common.project": { tr: "Proje", en: "Project", ru: "Проект" },
  "common.all": { tr: "Tümü (yetkili)", en: "All (permitted)", ru: "Все (доступные)" },
  "common.loading": { tr: "Yükleniyor…", en: "Loading…", ru: "Загрузка…" },
  "common.demo": {
    tr: "SENTETİK DEMO VERİSİ — gerçek kişi veya şirket verisi değildir",
    en: "SYNTHETIC DEMO DATA — not real people or companies",
    ru: "СИНТЕТИЧЕСКИЕ ДЕМО-ДАННЫЕ — не реальные люди или компании",
  },
  "status.ok": { tr: "Hesaplandı", en: "Calculated", ru: "Рассчитано" },
  "status.not_computable": { tr: "Hesaplanamaz (payda 0)", en: "Not computable (zero denominator)", ru: "Не вычисляется" },
  "status.insufficient_data": { tr: "Yetersiz veri", en: "Insufficient data", ru: "Недостаточно данных" },
  "status.suppressed": { tr: "Gizlendi (küçük grup)", en: "Suppressed (small group)", ru: "Скрыто (малая группа)" },
  "kpi.quality": { tr: "Veri kalitesi", en: "Data quality", ru: "Качество данных" },
  "kpi.formula": { tr: "Formül", en: "Formula", ru: "Формула" },
  "kpi.lineage": { tr: "Veri kökeni", en: "Lineage", ru: "Происхождение данных" },
  "kpi.annualized": { tr: "yıllık", en: "annualised", ru: "годовой" },
  "login.title": { tr: "Oturum açın", en: "Sign in", ru: "Вход" },
  "login.email": { tr: "E-posta", en: "Email", ru: "Эл. почта" },
  "login.password": { tr: "Parola", en: "Password", ru: "Пароль" },
  "login.submit": { tr: "Giriş yap", en: "Sign in", ru: "Войти" },
  "dash.title": { tr: "Üst Yönetim Paneli", en: "Executive Dashboard", ru: "Панель руководства" },
  "dash.headcountTrend": { tr: "Aktif personel trendi", en: "Active headcount trend", ru: "Динамика численности" },
  "dash.turnoverTrend": { tr: "Aylık devir oranı (%)", en: "Monthly turnover (%)", ru: "Текучесть по месяцам (%)" },
  "dash.otAbs": { tr: "Fazla mesai ve devamsızlık (%)", en: "Overtime & absenteeism (%)", ru: "Сверхурочные и абсентеизм (%)" },
  "dash.byEntity": { tr: "Firma bazında personel", en: "Headcount by company", ru: "Численность по компаниям" },
  "dash.forecast": { tr: "Personel tahmini (3 ay)", en: "Headcount forecast (3 months)", ru: "Прогноз численности (3 мес.)" },
  "dash.actions": { tr: "Öncelikli aksiyonlar", en: "Priority actions", ru: "Приоритетные меры" },
  "dash.risks": { tr: "Kritik riskler", en: "Critical risks", ru: "Критические риски" },
  "ai.placeholder": { tr: "Bir soru sorun…", en: "Ask a question…", ru: "Задайте вопрос…" },
  "ai.ask": { tr: "Sor", en: "Ask", ru: "Спросить" },
  "ai.notice": {
    tr: "Sayılar onaylı KPI motorundan gelir. Asistan bireysel işten çıkarma, ücret, disiplin veya terfi kararı üretmez.",
    en: "Numbers come from the approved KPI engine. The assistant never makes individual dismissal, pay, discipline or promotion decisions.",
    ru: "Числа берутся из утверждённого KPI-движка. Ассистент не принимает индивидуальных кадровых решений.",
  },
} as const;

export type Key = keyof typeof dict;

const Ctx = createContext<{ locale: Locale; setLocale: (l: Locale) => void; t: (k: Key) => string }>({
  locale: "tr",
  setLocale: () => {},
  t: (k) => dict[k].tr,
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [saved, setSaved] = usePersisted("hrswi.locale");
  const locale: Locale = saved === "en" || saved === "ru" || saved === "tr" ? saved : "tr";
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);
  return <Ctx.Provider value={{ locale, setLocale: setSaved, t: (k) => dict[k][locale] }}>{children}</Ctx.Provider>;
}

export const useI18n = () => useContext(Ctx);
