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
let manageAvailable = false;
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
  page.on("pageerror", (e) => errors.push(`${e.message.slice(0, 120)} @ ${page.url()}`));
  page.on("console", (m) => {
    if (m.type() !== "error") return;
    // while the data-management API is not deployed its 404s are expected and rendered as an ErrorBox
    if (!manageAvailable && /\/api\/v1\/manage\//.test(m.location()?.url ?? "")) return;
    errors.push(m.text());
  });

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
  const lineageOk = await page.getByText("Lineage").first().waitFor({ timeout: 20000 }).then(() => true, () => false);
  check("KPI detail shows lineage + formula versions", lineageOk);
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

  // module pages (formerly "planned")
  for (const [path, title, minTiles, shot] of [
    ["/hse", "İş Sağlığı ve Güvenliği", 2, "07-hse"],
    ["/training", "Eğitim ve Yetkinlik", 2, "08-training"],
    ["/cost", "Maliyet Yönetimi", 2, "09-cost"],
    ["/planning", "İş Gücü Planlama", 1, "10-planning"],
  ]) {
    await page.goto(`${WEB}${path}`);
    await page.getByRole("heading", { name: title, level: 1 }).waitFor({ timeout: 20000 });
    await page.locator("section[aria-label=KPI] a").nth(minTiles - 1).waitFor({ timeout: 20000 });
    const n = await page.locator("section[aria-label=KPI] a").count();
    await page.locator("canvas").first().waitFor({ timeout: 20000 });
    check(`${path} renders KPI tiles + chart`, n >= minTiles, `tiles=${n}`);
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${out}/${shot}.png`, fullPage: true });
  }
  await page.goto(`${WEB}/hse`);
  await page.getByText("LTIFR ve TRIR ayrı ölçülerdir.").waitFor({ timeout: 20000 });
  check("hse explains separate measures + occupation limitation", await page.getByText("Meslek grubu filtresi").isVisible());
  await page.goto(`${WEB}/cost`);
  await page.getByText("Hesaplanamayan göstergeler").waitFor({ timeout: 20000 });
  check("cost renders insufficient_data note (mixed units)", await page.getByText("Karışık birimler").first().isVisible());
  await page.goto(`${WEB}/planning`);
  await page.getByRole("radio", { name: /12/ }).click();
  await page.locator("tbody tr").nth(11).waitFor({ timeout: 20000 });
  check("planning horizon 12 → 12 forecast rows labelled TAHMİN", (await page.locator("tbody tr").count()) === 12 && (await page.getByText("TAHMİN").count()) >= 12);
  await page.waitForTimeout(1600); // let the ECharts line animation finish
  await page.screenshot({ path: `${out}/10-planning-12.png`, fullPage: true });

  // people: accessible terminate dialog (opened and dismissed with Esc - nothing is submitted)
  await page.goto(`${WEB}/people`);
  await page.getByRole("button", { name: /^Sonlandır:/ }).first().click();
  const dlg = page.getByRole("dialog", { name: "İstihdamı sonlandır" });
  await dlg.waitFor();
  await dlg.getByText("record_version").waitFor({ timeout: 20000 });
  const focusedInside = await page.evaluate(() => !!document.activeElement?.closest("dialog"));
  await page.screenshot({ path: `${out}/11-people-terminate-dialog.png` });
  await page.keyboard.press("Escape");
  await dlg.waitFor({ state: "hidden" });
  check("people terminate dialog: labelled, focus inside, Esc closes", focusedInside);

  // administration
  manageAvailable = await page.evaluate(async () => {
    const r = await fetch("http://localhost:8000/api/v1/manage/users", { headers: { Authorization: `Bearer ${localStorage.getItem("hrswi.token")}` } });
    return r.ok;
  });
  check("admin nav item visible for admin", await page.getByRole("link", { name: "Sistem Yönetimi" }).isVisible());
  await page.goto(`${WEB}/admin`);
  await page.getByRole("heading", { name: "Sistem Yönetimi", level: 1 }).waitFor({ timeout: 20000 });
  if (manageAvailable) {
    await page.locator("[data-testid=user-row]").first().waitFor({ timeout: 20000 });
    check("admin users table lists users", (await page.locator("[data-testid=user-row]").count()) >= 5);
    const email = `e2e.${Date.now()}@demo.local`;
    await page.getByRole("button", { name: "Kullanıcı oluştur" }).click();
    const cd = page.getByRole("dialog", { name: "Kullanıcı oluştur" });
    await cd.getByLabel("E-posta").fill(email);
    await cd.getByLabel("Ad soyad").fill("E2E Test Kullanıcısı");
    await cd.getByRole("button", { name: "Oluştur" }).click();
    const pw = page.getByTestId("initial-password");
    await pw.waitFor({ timeout: 20000 });
    const shown = (await pw.inputValue()).length >= 8;
    await page.screenshot({ path: `${out}/12-admin-created.png` });
    await page.getByRole("button", { name: "Tamam" }).click();
    await page.getByText(email).waitFor({ timeout: 20000 });
    check("admin: create user shows one-time password and lists the user", shown);
    // grant a company-scoped role, then revoke it (with confirmation)
    const row = page.locator("[data-testid=user-row]", { hasText: email });
    await row.getByRole("button", { name: "Rol ver" }).click();
    const gd = page.getByRole("dialog", { name: "Rol ata" });
    await gd.locator("select").first().selectOption("HR_SPECIALIST");
    await gd.locator("select").nth(1).selectOption({ index: 1 });
    await gd.getByRole("button", { name: "Rol ver" }).click();
    await gd.waitFor({ state: "hidden", timeout: 20000 });
    const revokeBtn = row.getByRole("button", { name: /^Geri al HR_SPECIALIST/ });
    await revokeBtn.waitFor({ timeout: 20000 });
    check("admin: grant role adds a role chip", true);
    await revokeBtn.click();
    const rd = page.getByRole("dialog", { name: "Rol atamasını geri al" });
    await rd.getByRole("button", { name: "Geri al" }).click();
    await rd.waitFor({ state: "hidden", timeout: 20000 });
    await revokeBtn.waitFor({ state: "detached", timeout: 20000 });
    check("admin: revoke removes the active role chip", true);
    // cleanup: deactivate the test user (users are never deleted; deactivation also revokes tokens)
    const cleaned = await page.evaluate(async ({ email, api }) => {
      const t = localStorage.getItem("hrswi.token");
      const h = { Authorization: `Bearer ${t}` };
      const users = await (await fetch(`${api}/api/v1/manage/users`, { headers: h })).json();
      const u = users.find((x) => x.email === email);
      return u ? (await fetch(`${api}/api/v1/manage/users/${u.id}/deactivate`, { method: "POST", headers: h })).status : 0;
    }, { email, api: process.env.API_URL ?? "http://localhost:8000" });
    check("admin: test user deactivated (cleanup)", cleaned === 200, `status=${cleaned}`);
  } else {
    await page.getByRole("alert").first().waitFor({ timeout: 20000 });
    check("admin: manage API unavailable → graceful error (SKIPPED create-user flow)", true, "(/api/v1/manage/users not deployed)");
  }
  await page.screenshot({ path: `${out}/12-admin.png`, fullPage: true });
  await page.goto(`${WEB}/imports`);
  await page.getByRole("heading", { name: "Kimlik eşleştirme kuyruğu" }).waitFor({ timeout: 20000 });
  check("imports shows identity review queue section", true);
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${out}/13-imports-idr.png`, fullPage: true });

  // leave import through the UI (B-07): pick an active employee via the API, upload a CSV with a diagnosis column
  const target = await page.evaluate(async (api) => {
    const h = { Authorization: `Bearer ${localStorage.getItem("hrswi.token")}` };
    const ents = await (await fetch(`${api}/api/v1/org/legal-entities`, { headers: h })).json();
    const sub = ents.find((e) => e.code === "SUB01");
    const ppl = await (await fetch(`${api}/api/v1/people?entity_id=${sub.id}&page_size=1`, { headers: h })).json();
    return { sicil: ppl.items[0].employee_number, code: sub.code };
  }, process.env.API_URL ?? "http://localhost:8000");
  const day = String(1 + (Date.now() % 28)).padStart(2, "0");
  const csv = `Sicil;Firma;İzin Türü;Başlangıç;Bitiş;Saat;Tanı;Not\n${target.sicil};${target.code};Yıllık izin;2027-01-${day};2027-01-${day};9;X;${Date.now()}\n`;
  await page.getByLabel("Veri türü").selectOption("leave");
  await page.getByLabel("File").setInputFiles({ name: "izin-e2e.csv", mimeType: "text/csv", buffer: Buffer.from(csv) });
  await page.getByText("okunmadan atıldı").waitFor({ timeout: 20000 });
  check("leave import: diagnosis column dropped before storage", await page.getByText("Tanı", { exact: true }).count() >= 1);
  await page.getByRole("button", { name: "Validate" }).click();
  await page.getByText(/valid 1/).waitFor({ timeout: 20000 });
  await page.getByRole("button", { name: "Commit" }).click();
  await page.getByText("committed 1").waitFor({ timeout: 20000 });
  await page.locator("tbody tr", { hasText: "izin-e2e.csv" }).first().getByText("committed").waitFor({ timeout: 20000 });
  check("leave import: validate + commit through the UI (batch list refreshed)", true);
  await page.screenshot({ path: `${out}/14-leave-import.png`, fullPage: true });

  // exec: no admin menu, direct URL shows no-access
  const cx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const px = await cx.newPage();
  await login(px, "exec@demo.local");
  await px.getByText("KPI-016").first().waitFor({ timeout: 20000 });
  check("exec: admin nav item absent", !(await px.getByRole("link", { name: "Sistem Yönetimi" }).count()));
  await px.goto(`${WEB}/admin`);
  await px.getByText("Bu ekrana erişim yetkiniz yok").waitFor({ timeout: 20000 });
  check("exec: /admin shows no-access message", true);
  await cx.close();

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
