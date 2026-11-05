// End-to-end smoke test against a running stack (API :8000, web :3000) with seeded demo data.
// Usage: node e2e/smoke.mjs [outDir]
import { createRequire } from "node:module";
const { chromium, devices } = createRequire(import.meta.url)("playwright");

const WEB = process.env.WEB_URL ?? "http://localhost:3000";
const out = process.argv[2] ?? "e2e-shots";
const results = [];
const check = (name, ok, info = "") => { results.push({ name, ok, info }); console.log(`${ok ? "PASS" : "FAIL"}  ${name} ${info}`); };

const browser = await chromium.launch();
const errors = [];
async function login(page, email) {
  await page.goto(`${WEB}/login`);
  await page.getByLabel(/E-posta|Email/).fill(email);
  await page.getByLabel(/Parola|Password/).fill("Demo!2345");
  await page.getByRole("button", { name: /Giriş yap|Sign in/ }).click();
  await page.waitForURL(`${WEB}/`);
}
try {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });

  // wrong password stays on login with an error
  await page.goto(`${WEB}/login`);
  await page.getByLabel(/E-posta|Email/).fill("admin@demo.local");
  await page.getByLabel(/Parola|Password/).fill("wrong");
  await page.getByRole("button", { name: /Giriş yap|Sign in/ }).click();
  await page.getByRole("alert").waitFor();
  check("login rejects wrong password", page.url().includes("/login"));
  errors.length = 0; // the 401 above is expected

  await login(page, "admin@demo.local");
  await page.getByText("KPI-016").first().waitFor({ timeout: 20000 });
  const tiles = await page.locator("section[aria-label=KPI] a").count();
  check("executive dashboard renders KPI tiles from API", tiles >= 11, `tiles=${tiles}`);
  await page.locator("canvas").first().waitFor();
  check("charts rendered", (await page.locator("canvas").count()) >= 4);
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/01-dashboard.png`, fullPage: true });

  await page.goto(`${WEB}/kpis/KPI-001`);
  await page.getByText("Formula versions").waitFor();
  await page.getByText("By company (same filters)").waitFor({ timeout: 20000 });
  check("KPI detail shows lineage + formula versions", await page.getByText("Lineage").first().isVisible());
  await page.screenshot({ path: `${out}/02-kpi-detail.png`, fullPage: true });

  await page.goto(`${WEB}/assistant`);
  await page.getByRole("button", { name: "Son altı ayda personel devir oranı neden yükseldi?" }).click();
  await page.getByText("turnover_trend").waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Önümüzdeki üç ay için kaç ilave çalışana ihtiyaç duyulabilir?" }).click();
  await page.getByText("FORECAST / TAHMİN").waitFor({ timeout: 20000 });
  check("assistant answers and labels forecast", true);
  await page.screenshot({ path: `${out}/03-assistant.png`, fullPage: true });

  await page.goto(`${WEB}/contractors`);
  await page.getByText("data sharing granted").first().waitFor({ timeout: 20000 });
  await page.screenshot({ path: `${out}/04-contractors.png`, fullPage: true });

  await page.goto(`${WEB}/sustainability`);
  await page.getByText("not a compliance", { exact: false }).first().waitFor({ timeout: 20000 });
  check("sustainability shows coverage disclaimer", true);

  // language + dark mode
  await page.goto(`${WEB}/`);
  await page.getByLabel("Language").selectOption("en");
  await page.getByRole("button", { name: "Toggle theme" }).click();
  await page.getByText("Executive Dashboard").waitFor();
  await page.getByText("Employee Turnover Rate").first().waitFor({ timeout: 20000 });
  check("i18n switch to EN (UI + backend KPI names)", true);
  check("dark mode applied", await page.evaluate(() => document.documentElement.classList.contains("dark")));
  await page.getByText("KPI-016").first().waitFor({ timeout: 20000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `${out}/05-dashboard-dark-en.png`, fullPage: true });

  // scope: subcontractor manager only sees own + shared tier-2
  const ctx2 = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const p2 = await ctx2.newPage();
  await login(p2, "sub01.manager@demo.local");
  await p2.goto(`${WEB}/contractors`);
  await p2.locator("tbody tr").first().waitFor({ timeout: 20000 });
  const names = await p2.locator("tbody tr td:first-child").allInnerTexts();
  check("sub01 sees only SUB01 + its tier-2 firms", names.length === 3 && names.every((n) => /01|11|12/.test(n)), `rows=${names.length}`);
  check("restricted user has no cost menu / report export", !(await p2.getByText("Export").count()));

  // mobile
  const m = await browser.newContext({ ...devices["iPhone 13"] });
  const mp = await m.newPage();
  await login(mp, "exec@demo.local");
  await mp.getByText("KPI-016").first().waitFor({ timeout: 20000 });
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check("mobile: no horizontal page overflow", !overflow);
  await mp.screenshot({ path: `${out}/06-mobile.png` });

  check("no browser console/page errors", errors.length === 0, errors.slice(0, 3).join(" | "));
} finally {
  await browser.close();
}
const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
process.exit(failed.length ? 1 : 0);
