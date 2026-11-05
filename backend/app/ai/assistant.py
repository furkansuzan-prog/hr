"""HR Intelligence Assistant (ADR-010).

A tool-routing assistant: question -> intent -> approved services (KPI engine, forecast, anomaly).
All numbers in answers are injected from service outputs; the narrative never invents figures.
The user's AccessContext is applied to every tool call. Prohibited high-impact individual decisions are refused.
An optional LLM (disabled by default) may be plugged in for intent parsing/narrative on *aggregated* outputs only.
"""
import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.service import headcount_forecast, kpi_anomalies
from app.analytics import engine
from app.analytics.domain import pearson
from app.models import AiAnalysisRun, ImprovementAction
from app.security.access import AccessContext

PROHIBITED = [
    r"işten çıkar", r"kov(ul|al)?", r"kimi çıkar", r"terfi", r"maaş(ını)? belirle", r"ücret(ini)? belirle", r"disiplin",
    r"\bfire\b", r"\blay ?off\b", r"who should (we|i) (fire|dismiss)", r"promot", r"set (his|her|their) salary",
    r"disciplin", r"уволи", r"повышени", r"зарплат[уы] (назнач|определ)", r"дисциплин",
    r"(kişi|çalışan|employee|сотрудник)\w* risk (puan|score)",
]

INTENTS: list[tuple[str, list[str]]] = [
    ("action_results", ["aksiyon", "action", "мероприят", "действи"]),
    ("training_quality", ["eğitim.*kalite", "kalite.*eğitim", "training.*quality", "quality.*training", "обучен.*качеств"]),
    ("overtime_output", ["fazla mesai", "overtime", "сверхуроч"]),
    ("forecast_headcount", ["ihtiyaç", "tahmin", "ilave", "forecast", "need", "how many additional", "прогноз", "потребн"]),
    ("plan_deviation", ["planlanan", "plan sapma", "planned", "plan deviation", "план"]),
    ("contractor_risk", ["alt yüklenici", "taşeron", "subcontractor", "contractor", "субподряд", "подрядчик"]),
    ("productivity_loss", ["verimlilik", "iş paket", "productivity", "work package", "производительн"]),
    ("safety", ["kaza", "isg", "iş güvenliği", "safety", "ltifr", "trir", "injur", "травм", "безопасн"]),
    ("absenteeism", ["devamsızlık", "absentee", "absence", "абсентеизм", "прогул"]),
    ("turnover_trend", ["devir", "ayrılış", "turnover", "attrition", "текучест", "увольнен"]),
]

T = {
    "refuse": {
        "tr": "Bu talep bireysel bir çalışan hakkında yüksek etkili bir karar (işten çıkarma, ücret, disiplin, terfi veya "
              "bireysel risk puanı) içeriyor. Asistan bu tür kararları üretmez; insan değerlendirmesi ve gerekirse hukuki "
              "inceleme gerekir. Toplu ve açıklanabilir göstergeleri sorabilirsiniz.",
        "en": "This request involves a high-impact decision about an individual (dismissal, pay, discipline, promotion or an "
              "individual risk score). The assistant does not produce such decisions; human review and, where needed, legal "
              "review are required. You can ask about aggregated, explainable indicators instead.",
        "ru": "Запрос касается решения с высоким воздействием на конкретного сотрудника (увольнение, оплата, дисциплина, "
              "повышение или индивидуальный риск-скоринг). Ассистент не принимает такие решения; требуется оценка человеком "
              "и при необходимости юридическая проверка.",
    },
    "unknown": {
        "tr": "Soruyu onaylı analiz araçlarından birine eşleyemedim. Örnek: 'Son altı ayda devir oranı neden yükseldi?', "
              "'Önümüzdeki üç ay için kaç ilave çalışana ihtiyaç var?'",
        "en": "I could not map the question to an approved analysis tool. Try e.g. 'Why did turnover rise in the last six "
              "months?' or 'How many additional employees will we need in the next three months?'",
        "ru": "Не удалось сопоставить вопрос с утверждённым инструментом анализа. Пример: «Почему выросла текучесть за "
              "последние шесть месяцев?»",
    },
    "corr_caveat": {
        "tr": "Korelasyon nedensellik değildir; proje evresi, iş kapsamı ve meslek dağılımı gibi karıştırıcı etkenler kontrol edilmedi.",
        "en": "Correlation is not causation; confounders such as project phase, scope of work and occupation mix are not controlled.",
        "ru": "Корреляция не означает причинность; влияющие факторы (фаза проекта, объём работ, структура профессий) не учтены.",
    },
    "forecast_caveat": {
        "tr": "Bu değerler TAHMİNDİR, gerçekleşmiş veri değildir. Model geçmiş eğilimi sürdürür; planlanan proje evresi değişiklikleri dahil değildir.",
        "en": "These values are FORECASTS, not actuals. The model extrapolates past trend; planned project phase changes are not included.",
        "ru": "Это ПРОГНОЗ, а не фактические данные. Модель экстраполирует прошлый тренд.",
    },
}


def detect_lang(q: str, default: str) -> str:
    if re.search(r"[а-яА-ЯёЁ]", q):
        return "ru"
    if re.search(r"[çğıöşüÇĞİÖŞÜ]", q) or re.search(r"\b(ne|neden|hangi|kaç|nasıl|son|ay)\b", q.lower()):
        return "tr"
    if re.search(r"\b(why|which|how|what|the|last)\b", q.lower()):
        return "en"
    return default if default in ("tr", "en", "ru") else "en"


def classify(q: str) -> str:
    ql = q.lower()
    if any(re.search(p, ql) for p in PROHIBITED):
        return "prohibited"
    for intent, pats in INTENTS:
        if any(re.search(p, ql) for p in pats):
            return intent
    return "unknown"


def _months_back(as_of: date, n: int) -> date:
    m = as_of.month - 1 - (n - 1)
    return date(as_of.year + m // 12, m % 12 + 1, 1)


def _fmt(v, unit=""):
    if v is None:
        return "—"
    return f"{v:,.2f}{unit}".replace(",", " ")


def _src(r: dict) -> dict:
    return {"kpi": r["code"], "formula_version": r["formula"]["version"], "status": r["status"]}


def _series_answer(db, ctx, code, ps, pe, lang, label):
    series = engine.monthly_series(db, ctx, code, ps, pe)
    first = next((s for s in series if s["value"] is not None), None)
    last = next((s for s in reversed(series) if s["value"] is not None), None)
    data = [{"period": s["period_start"][:7], "value": s["value"], "status": s["status"]} for s in series]
    return series, first, last, data


def handle(db: Session, ctx: AccessContext, question: str, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    lang = detect_lang(question, ctx.locale)
    intent = classify(question)
    tools: list[str] = []
    out: dict = {"intent": intent, "language": lang, "kind": "actual", "data": {}, "caveats": [], "sources": []}
    last_full = date(as_of.year, as_of.month, 1) - timedelta(days=1)
    ps6 = _months_back(last_full, 6)

    try:
        if intent == "prohibited":
            out["answer"] = T["refuse"][lang]
            out["refused"] = True

        elif intent == "turnover_trend":
            tools.append("kpi.monthly_series:KPI-001")
            _, first, last, data = _series_answer(db, ctx, "KPI-001", ps6, last_full, lang, "turnover")
            total = engine.evaluate(db, ctx, "KPI-001", ps6, last_full)
            tools.append("kpi.by_entity:KPI-001")
            ents = [e for e in engine.by_entity(db, ctx, "KPI-001", ps6, last_full) if e["value"] is not None]
            ents.sort(key=lambda e: e["value"], reverse=True)
            cats = (total.get("breakdown") or {}).get("by_termination_category", {})
            top_cat = max(((k, v) for k, v in cats.items() if v), key=lambda kv: kv[1], default=(None, 0))
            anomalies = kpi_anomalies(db, ctx, "KPI-001", _months_back(last_full, 18), last_full)["anomalies"]
            tools.append("anomaly.robust_z:KPI-001")
            out["data"] = {"monthly": data, "period_total": total["value"], "by_category": cats,
                           "top_entities": [{"entity": e["legal_entity"]["name"], "value": e["value"]} for e in ents[:5]],
                           "anomalies": anomalies}
            out["sources"] = [_src(total)]
            change = (last["value"] - first["value"]) if first and last else None
            if lang == "tr":
                out["answer"] = (f"Son 6 ayda (KPI-001) dönem devir oranı %{_fmt(total['value'])}. Aylık oran "
                                 f"{first['period_start'][:7] if first else '—'} ayında %{_fmt(first and first['value'])}, "
                                 f"{last['period_start'][:7] if last else '—'} ayında %{_fmt(last and last['value'])} "
                                 f"(fark {_fmt(change)} puan). En büyük ayrılış kategorisi: {top_cat[0] or '—'} ({top_cat[1]}). "
                                 + (f"En yüksek devir: {ents[0]['legal_entity']['name']} (%{_fmt(ents[0]['value'])})." if ents else ""))
            elif lang == "ru":
                out["answer"] = (f"За последние 6 месяцев текучесть (KPI-001) составила {_fmt(total['value'])}%. "
                                 f"Основная категория увольнений: {top_cat[0] or '—'} ({top_cat[1]}).")
            else:
                out["answer"] = (f"Over the last 6 months turnover (KPI-001) was {_fmt(total['value'])}%. Monthly rate moved "
                                 f"from {_fmt(first and first['value'])}% to {_fmt(last and last['value'])}% "
                                 f"(change {_fmt(change)} pts). Largest separation category: {top_cat[0] or '—'} ({top_cat[1]}). "
                                 + (f"Highest turnover: {ents[0]['legal_entity']['name']} ({_fmt(ents[0]['value'])}%)." if ents else ""))
            out["caveats"].append({"tr": "Ayrılış kategorileri nedenleri açıklar; kök neden için çıkış görüşmesi/anket verisi gerekir.",
                                   "en": "Separation categories describe, not explain; root cause needs exit-interview/survey data.",
                                   "ru": "Категории увольнений описывают, но не объясняют причины."}[lang])
            if cats.get("unknown"):
                out["caveats"].append(f"unknown reason: {cats['unknown']}")

        elif intent == "plan_deviation":
            tools.append("kpi.by_entity:KPI-006")
            rows = [r for r in engine.by_entity(db, ctx, "KPI-006", ps6, last_full) if r["status"] == "ok"]
            rows.sort(key=lambda r: abs(r["value"]), reverse=True)
            out["data"] = {"by_entity": [{"entity": r["legal_entity"]["name"], "deviation": r["value"],
                                          "deviation_pct": r["breakdown"]["deviation_pct"],
                                          "planned_avg": r["breakdown"]["planned_avg"],
                                          "actual_avg": r["breakdown"]["actual_avg"]} for r in rows]}
            out["sources"] = [_src(r) for r in rows[:1]]
            top = rows[0] if rows else None
            if top:
                msg = {"tr": f"Son 6 ayda en yüksek plan sapması {top['legal_entity']['name']}: {_fmt(top['value'])} kişi "
                             f"(%{_fmt(top['breakdown']['deviation_pct'])}).",
                       "en": f"Largest plan deviation over the last 6 months: {top['legal_entity']['name']} with "
                             f"{_fmt(top['value'])} persons ({_fmt(top['breakdown']['deviation_pct'])}%).",
                       "ru": f"Наибольшее отклонение от плана: {top['legal_entity']['name']} — {_fmt(top['value'])} чел."}
                out["answer"] = msg[lang]
            else:
                out["answer"] = {"tr": "Kapsamınızda iş gücü planı bulunan birim yok.", "en": "No workforce plan exists in your scope.",
                                 "ru": "В вашей области нет плана персонала."}[lang]

        elif intent == "forecast_headcount":
            tools.append("forecast.headcount")
            fc = headcount_forecast(db, ctx, as_of=last_full, horizon=3)
            out["kind"] = "forecast"
            out["data"] = fc
            if fc["status"] != "ok":
                out["answer"] = {"tr": f"Tahmin üretilemedi: {fc['note']}", "en": f"No forecast produced: {fc['note']}",
                                 "ru": f"Прогноз невозможен: {fc['note']}"}[lang]
            else:
                p = fc["forecast"][-1]
                out["answer"] = {
                    "tr": f"Mevcut eğilim sürerse 3 ay sonra aktif personel ~{_fmt(p['mean'])} (%80 aralık {_fmt(p['lo80'])}–"
                          f"{_fmt(p['hi80'])}) olabilir; bugüne göre {_fmt(fc['delta_at_horizon'])} kişi. Geriye dönük test MAPE: "
                          f"%{_fmt(fc['backtest_mape'])}. İhtiyaç planla karşılaştırılmalıdır.",
                    "en": f"If the current trend continues, active headcount in 3 months may be ~{_fmt(p['mean'])} (80% interval "
                          f"{_fmt(p['lo80'])}–{_fmt(p['hi80'])}), a change of {_fmt(fc['delta_at_horizon'])} vs today. "
                          f"Back-test MAPE: {_fmt(fc['backtest_mape'])}%. Compare against the workforce plan for true need.",
                    "ru": f"При сохранении тренда через 3 месяца численность ~{_fmt(p['mean'])} (80% интервал {_fmt(p['lo80'])}–"
                          f"{_fmt(p['hi80'])}).",
                }[lang]
            out["caveats"].append(T["forecast_caveat"][lang])

        elif intent == "overtime_output":
            tools += ["kpi.monthly_series:KPI-007", "kpi.monthly_series:KPI-005"]
            _, f7, l7, d7 = _series_answer(db, ctx, "KPI-007", ps6, last_full, lang, "ot")
            _, f5, l5, d5 = _series_answer(db, ctx, "KPI-005", ps6, last_full, lang, "prod")
            r, n = pearson([x["value"] for x in d7], [x["value"] for x in d5])
            out["data"] = {"overtime_rate": d7, "productivity_index": d5, "correlation": r, "n_months": n}
            out["answer"] = {
                "tr": f"Fazla çalışma oranı %{_fmt(f7 and f7['value'])} → %{_fmt(l7 and l7['value'])}; verimlilik endeksi "
                      f"{_fmt(f5 and f5['value'])} → {_fmt(l5 and l5['value'])}. Aylık korelasyon r={_fmt(r)} (n={n}). "
                      "Fazla mesai artışı verimliliği orantılı artırmıyorsa yorgunluk, duruş veya malzeme eksikliği incelenmelidir.",
                "en": f"Overtime rate {_fmt(f7 and f7['value'])}% → {_fmt(l7 and l7['value'])}%; productivity index "
                      f"{_fmt(f5 and f5['value'])} → {_fmt(l5 and l5['value'])}. Monthly correlation r={_fmt(r)} (n={n}). "
                      "If overtime rises without proportional output, review fatigue, downtime and material shortages.",
                "ru": f"Сверхурочные {_fmt(f7 and f7['value'])}% → {_fmt(l7 and l7['value'])}%; индекс производительности "
                      f"{_fmt(f5 and f5['value'])} → {_fmt(l5 and l5['value'])}. r={_fmt(r)} (n={n}).",
            }[lang]
            out["caveats"].append(T["corr_caveat"][lang])
            out["caveats"].append({"tr": "Verimlilik ile çalışan refahı/İSG arasında gerilim olabilir: yüksek fazla mesai risk göstergesidir.",
                                   "en": "Efficiency vs wellbeing/safety tension: sustained high overtime is itself a risk indicator.",
                                   "ru": "Высокие сверхурочные — фактор риска для здоровья и безопасности."}[lang])

        elif intent == "training_quality":
            tools += ["kpi.by_entity:KPI-008", "kpi.by_entity:KPI-013"]
            tr = {e["legal_entity"]["id"]: e for e in engine.by_entity(db, ctx, "KPI-008", ps6, last_full)}
            rw = {e["legal_entity"]["id"]: e for e in engine.by_entity(db, ctx, "KPI-013", ps6, last_full)}
            pairs = [(tr[k]["value"], rw[k]["value"], tr[k]["legal_entity"]["name"]) for k in tr if k in rw
                     and tr[k]["value"] is not None and rw[k]["value"] is not None]
            r, n = pearson([p[0] for p in pairs], [p[1] for p in pairs])
            out["data"] = {"pairs": [{"entity": p[2], "training_h_per_emp": p[0], "rework_rate": p[1]} for p in pairs],
                           "pearson_r": r, "n_entities": n}
            out["answer"] = {
                "tr": f"Firma düzeyinde çalışan başına eğitim saati ile yeniden iş oranı arasında r={_fmt(r)} (n={n} firma) "
                      "korelasyon bulundu." + (" Örneklem küçük; sonuç güvenilir değildir." if n < 10 else ""),
                "en": f"At entity level, training hours per employee and rework rate correlate at r={_fmt(r)} (n={n} entities)."
                      + (" Sample is small; result is not reliable." if n < 10 else ""),
                "ru": f"Корреляция часов обучения и доли переделок: r={_fmt(r)} (n={n}).",
            }[lang]
            out["caveats"].append(T["corr_caveat"][lang])

        elif intent == "contractor_risk":
            tools += ["kpi.by_entity:KPI-001", "kpi.by_entity:KPI-007", "kpi.by_entity:KPI-014A", "kpi.by_entity:KPI-009"]
            metrics = {c: {e["legal_entity"]["id"]: e for e in engine.by_entity(db, ctx, c, ps6, last_full)}
                       for c in ("KPI-001", "KPI-007", "KPI-014A", "KPI-009")}
            rows = []
            subs = [eid for eid, e in metrics["KPI-001"].items() if e["legal_entity"]["type"] == "subcontractor"]

            def peer_median(code):
                vals = sorted(metrics[code][i]["value"] for i in subs if i in metrics[code]
                              and metrics[code][i]["value"] is not None)
                return vals[len(vals) // 2] if vals else None

            med = {c: peer_median(c) for c in ("KPI-001", "KPI-014A")}
            for eid in subs:
                e = metrics["KPI-001"][eid]
                vals = {c: metrics[c][eid]["value"] for c in metrics if eid in metrics[c]}
                flags = []
                # turnover / LTIFR are judged relative to peers (1.5x peer median); overtime and competency on
                # absolute, period-independent thresholds
                if med["KPI-001"] and (vals.get("KPI-001") or 0) > 1.5 * med["KPI-001"]:
                    flags.append("high_turnover")
                if (vals.get("KPI-007") or 0) > 15:
                    flags.append("excessive_overtime")
                if med["KPI-014A"] and (vals.get("KPI-014A") or 0) > 1.5 * med["KPI-014A"]:
                    flags.append("high_ltifr")
                if vals.get("KPI-009") is not None and vals["KPI-009"] < 80:
                    flags.append("competency_gap")
                rows.append({"entity": e["legal_entity"]["name"], **vals, "flags": flags})
            rows.sort(key=lambda r: len(r["flags"]), reverse=True)
            out["data"] = {"subcontractors": rows, "rules": {"KPI-001": "> 1.5 x peer median", "KPI-007": "> 15 %",
                                                                  "KPI-014A": "> 1.5 x peer median", "KPI-009": "< 80 %"},
                           "peer_medians": med}
            flagged = [r for r in rows if r["flags"]]
            out["answer"] = {
                "tr": f"Kapsamınızdaki {len(rows)} alt yükleniciden {len(flagged)} tanesinde en az bir risk işareti var. "
                      + ("; ".join(f"{r['entity']}: {', '.join(r['flags'])}" for r in flagged[:5])),
                "en": f"{len(flagged)} of {len(rows)} subcontractors in your scope have at least one risk flag. "
                      + ("; ".join(f"{r['entity']}: {', '.join(r['flags'])}" for r in flagged[:5])),
                "ru": f"У {len(flagged)} из {len(rows)} субподрядчиков есть хотя бы один флаг риска.",
            }[lang]
            out["caveats"].append({"tr": "Firmaları karşılaştırırken iş kapsamı, meslek dağılımı ve proje evresi farklılıkları dikkate alınmalıdır.",
                                   "en": "Compare firms only with their scope of work, occupation mix and project phase in mind.",
                                   "ru": "Учитывайте различия в объёме работ, профессиях и фазе проекта."}[lang])

        elif intent in ("safety", "absenteeism", "productivity_loss"):
            code = {"safety": "KPI-014A", "absenteeism": "KPI-003", "productivity_loss": "KPI-005"}[intent]
            tools.append(f"kpi.by_entity:{code}")
            total = engine.evaluate(db, ctx, code, ps6, last_full)
            rows = [e for e in engine.by_entity(db, ctx, code, ps6, last_full) if e["value"] is not None]
            worst_high = code != "KPI-005"
            rows.sort(key=lambda e: e["value"], reverse=worst_high)
            out["data"] = {"total": total, "by_entity": [{"entity": e["legal_entity"]["name"], "value": e["value"]}
                                                         for e in rows]}
            out["sources"] = [_src(total)]
            name = total["name"]
            w = rows[0] if rows else None
            out["answer"] = {
                "tr": f"Son 6 ay {name}: {_fmt(total['value'])} {total['unit']}." + (
                    f" En olumsuz değer: {w['legal_entity']['name']} ({_fmt(w['value'])})." if w else ""),
                "en": f"Last 6 months {name}: {_fmt(total['value'])} {total['unit']}." + (
                    f" Least favourable: {w['legal_entity']['name']} ({_fmt(w['value'])})." if w else ""),
                "ru": f"За 6 месяцев {name}: {_fmt(total['value'])} {total['unit']}.",
            }[lang]
            if total["status"] != "ok":
                out["caveats"].append(total.get("note") or total["status"])

        elif intent == "action_results":
            tools.append("actions.list")
            q = select(ImprovementAction).where(ImprovementAction.tenant_id == ctx.tenant_id)
            scope = ctx.entity_filter("risk.read") if ctx.has("risk.read") else set()
            if scope is not None:
                q = q.where(ImprovementAction.legal_entity_id.in_(scope or {-1}))
            acts = list(db.execute(q.order_by(ImprovementAction.due_date.desc()).limit(20)).scalars())
            out["data"] = {"actions": [{"problem": a.problem, "kpi": a.kpi_code, "baseline": _f(a.baseline_value),
                                        "target": _f(a.target_value), "result": _f(a.result_value),
                                        "effectiveness": a.effectiveness, "status": a.status} for a in acts]}
            eff = sum(1 for a in acts if a.effectiveness == "effective")
            out["answer"] = {"tr": f"{len(acts)} aksiyondan {eff} tanesi 'etkili' olarak değerlendirildi.",
                             "en": f"{eff} of {len(acts)} actions were assessed as effective.",
                             "ru": f"{eff} из {len(acts)} мероприятий оценены как эффективные."}[lang]
        else:
            out["answer"] = T["unknown"][lang]
    except engine.ScopeDenied as e:
        out["answer"] = {"tr": "Bu analiz için yetkiniz yok.", "en": "You are not authorised for this analysis.",
                         "ru": "Нет прав на этот анализ."}[lang]
        out["refused"] = True
        out["caveats"].append(str(e))

    out["tools_used"] = tools
    out["llm_used"] = False
    db.add(AiAnalysisRun(tenant_id=ctx.tenant_id, user_id=ctx.user_id, question=question[:2000], intent=intent,
                         tools_used=tools, scope={"grants": sorted(ctx.permissions)}, refused=bool(out.get("refused")),
                         llm_used=False))
    db.commit()
    return out


def _f(v):
    return float(v) if v is not None else None


