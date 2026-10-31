// Optional browser regression check. The demo itself has no npm dependencies.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");
let chromium;
try { ({ chromium } = require("playwright")); }
catch { ({ chromium } = require(path.join(__dirname, "..", ".demo-tools", "node_modules", "playwright"))); }

const root = path.resolve(__dirname, "..");
const artifacts = path.join(root, ".demo-artifacts");
fs.mkdirSync(artifacts, { recursive: true });
const child = spawn(process.env.PYTHON || "python", ["demo.py", "--port", "0", "--no-browser"], { cwd: root, windowsHide: true });
let browser;

async function main() {
  const url = await new Promise((resolve, reject) => {
    let output = "";
    const timeout = setTimeout(() => reject(new Error(`Demo startup timeout: ${output}`)), 20000);
    child.on("error", reject);
    child.on("exit", (code) => { clearTimeout(timeout); reject(new Error(`Demo exited: ${code}, ${output}`)); });
    child.stdout.on("data", (data) => { output += data; const match = output.match(/http:\/\/127\.0\.0\.1:\d+\//); if (match) { clearTimeout(timeout); resolve(match[0]); } });
    child.stderr.on("data", (data) => { output += data; });
  });
  browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1050 } });
  const external = [], errors = [];
  await context.route("**/*", (route) => {
    if (new URL(route.request().url()).origin === new URL(url).origin) return route.continue();
    external.push(route.request().url()); return route.abort();
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
  await page.goto(url);
  await page.locator("#workspace").waitFor({ state: "visible" });
  assert.match(await page.locator("#pnl").innerText(), /270\.45/);
  assert.match(await page.locator("#trades").innerText(), /利確/);
  await page.screenshot({ path: path.join(artifacts, "desktop.png"), fullPage: true });
  const pixelChecks = await page.locator("canvas").evaluateAll((canvases) => canvases.map((canvas) => {
    const pixels = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data;
    let colored = 0;
    for (let i = 0; i < pixels.length; i += 4) if (pixels[i + 3] && Math.max(pixels[i], pixels[i + 1], pixels[i + 2]) - Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) > 25) colored++;
    return { width: canvas.width, height: canvas.height, colored };
  }));
  assert(pixelChecks.every((canvas) => canvas.colored > 200), JSON.stringify(pixelChecks));
  await page.locator("#cursor").fill("30");
  await page.locator("#cursor").dispatchEvent("input");
  assert.equal(await page.locator("#signal").innerText(), "新規約定");
  assert.equal(await page.locator("#count").innerText(), "0件");
  assert.match(await page.locator("#pnl").innerText(), /-.*20/);
  await page.locator("#step").click();
  assert.equal(await page.locator("#signal").innerText(), "保有継続");
  await page.locator("#restart").click();
  await page.locator("#toggle").click();
  const paused = await page.locator("#progress").innerText();
  await page.waitForTimeout(450);
  assert.equal(await page.locator("#progress").innerText(), paused);
  await page.locator("#speed").selectOption("80");
  await page.locator("#toggle").click();
  await page.waitForFunction(() => document.querySelector("#phase").textContent === "完了", null, { timeout: 10000 });
  assert.match(await page.locator("#pnl").innerText(), /270\.45/);
  await page.getByRole("tab", { name: "下抜け後の反転" }).click();
  assert.match(await page.locator("#pnl").innerText(), /-.*361\.48/);
  assert.match(await page.locator("#trades").innerText(), /空売り.*損切り/s);
  await page.screenshot({ path: path.join(artifacts, "loss.png"), fullPage: true });
  await page.getByRole("tab", { name: "レンジ", exact: true }).click();
  assert.equal(await page.locator("#count").innerText(), "0件");
  assert.match(await page.locator("#events").innerText(), /売買条件に未到達/);
  await page.getByRole("tab", { name: "上昇", exact: true }).click();
  const download = await page.request.get(new URL("download.json", url).href);
  const model = await download.json();
  assert.equal(model.allow_live_order, false);
  assert.equal(model.order_adapter, "paper");
  await page.reload(); await page.locator("#workspace").waitFor({ state: "visible" });
  assert.match(await page.locator("#pnl").innerText(), /270\.45/);
  const overflow = [];
  for (const [width, height] of [[390, 844], [320, 740], [768, 1024], [1440, 900]]) {
    await page.setViewportSize({ width, height });
    await page.locator("#settings").evaluate((element) => { element.parentElement.open = true; });
    const result = await page.evaluate(() => ({ document: document.documentElement.scrollWidth > window.innerWidth,
      elements: [...document.querySelectorAll("h1,h2,h3,button,.metrics strong,dd")].filter((element) => element.scrollWidth > element.clientWidth + 1).map((element) => element.outerHTML) }));
    if (result.document || result.elements.length) overflow.push({ width, ...result });
    if (width === 390) await page.screenshot({ path: path.join(artifacts, "mobile.png"), fullPage: true });
  }
  assert.deepEqual(overflow, []);
  assert.deepEqual(external, []);
  assert.deepEqual(errors, []);
  const report = { browser: browser.version(), fingerprint: model.fingerprint, pixelChecks, viewports: [320, 390, 768, 1440],
    scenarios: 3, playback: "passed", externalRequests: external, javascriptErrors: errors, overflow };
  fs.writeFileSync(path.join(artifacts, "browser-report.json"), JSON.stringify(report, null, 2));
  console.log(JSON.stringify(report, null, 2));
}

main().catch((error) => { console.error(error); process.exitCode = 1; }).finally(async () => {
  if (browser) await browser.close();
  child.kill();
});
