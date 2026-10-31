"use strict";

const $ = (id) => document.getElementById(id);
const yen = (n) => new Intl.NumberFormat("ja-JP", { style: "currency", currency: "JPY", maximumFractionDigits: 2 }).format(n);
const number = (n) => n == null ? "-" : new Intl.NumberFormat("ja-JP", { maximumFractionDigits: 2 }).format(n);
const percent = (n) => `${(n * 100).toFixed(2)}%`;
const time = (stamp) => stamp.slice(11, 16);
const labels = { WAIT: "見送り", BLOCKED: "見送り", ENTRY: "新規約定", EXIT: "決済", HOLD: "保有継続" };
const reasons = {
  "opening range is still forming": "寄り付き30分の価格帯を形成中",
  "breakout/breakdown or vwap condition not met": "価格帯を抜けず、売買条件に未到達",
  "opening range breakout above vwap": "寄り付き高値を上抜け、VWAP条件も成立",
  "opening range breakdown below vwap": "寄り付き安値を下抜け、VWAP条件も成立",
  "position open": "損切り・利確条件に未到達のため保有継続",
  "exit by target": "利確水準に到達したため決済",
  "exit by stop": "損切り水準に到達したため決済",
  "exit by eod": "固定の手仕舞い時刻に到達したため決済",
  "max trades per day reached": "1日1取引の上限に到達し、新規売買を見送り",
  "new entry window closed": "新規売買の受付時刻を終了",
};
let model, scenario, cursor = 0, timer = null;

function pause() {
  clearTimeout(timer);
  timer = null;
}

function play() {
  pause();
  if (cursor >= scenario.frames.length - 1) return;
  timer = setTimeout(() => { cursor += 1; play(); render(); }, Number($("speed").value));
}

function selectScenario(id) {
  pause();
  scenario = model.scenarios.find((s) => s.id === id);
  cursor = scenario.frames.length - 1;
  $("replay-panel").setAttribute("aria-labelledby", `tab-${id}`);
  document.querySelectorAll('[role="tab"]').forEach((button) => {
    const selected = button.dataset.id === id;
    button.setAttribute("aria-selected", String(selected));
    button.tabIndex = selected ? 0 : -1;
  });
  $("cursor").max = cursor;
  render();
}

function cell(row, value, className = "") {
  const td = document.createElement("td");
  td.textContent = value;
  td.className = className;
  row.append(td);
}

function render() {
  const f = scenario.frames[cursor];
  $("instrument").textContent = scenario.symbol;
  $("clock").textContent = `${f.timestamp} JST`;
  $("phase").textContent = timer ? "再生中" : cursor === scenario.frames.length - 1 ? "完了" : "一時停止";
  $("cursor").value = cursor;
  $("progress").textContent = `${cursor + 1} / ${scenario.frames.length}`;
  $("capital").textContent = yen(model.settings.sim_capital_yen);
  $("balance").textContent = yen(f.balance_yen);
  $("pnl").textContent = `${f.pnl_yen > 0 ? "+" : ""}${yen(f.pnl_yen)}`;
  $("pnl").className = f.pnl_yen > 0 ? "gain" : f.pnl_yen < 0 ? "loss" : "";
  $("count").textContent = `${f.closed_trades}件`;
  $("wins").textContent = `${f.wins}勝 / ${f.losses}敗`;
  $("drawdown").textContent = percent(f.drawdown);
  $("toggle").disabled = cursor === scenario.frames.length - 1;
  $("toggle").textContent = timer ? "\u23F8" : "\u25B6";
  $("toggle").title = timer ? "再生を一時停止" : "再生を再開";
  $("toggle").setAttribute("aria-label", $("toggle").title);
  $("step").disabled = cursor === scenario.frames.length - 1;
  $("signal").textContent = labels[f.signal] || f.signal;
  $("signal").dataset.signal = f.signal;
  $("reason").textContent = reasons[f.reason] || f.reason;
  $("raw-reason").textContent = f.reason;
  $("position").textContent = { LONG: "買い", SHORT: "空売り", FLAT: "未保有" }[f.position];
  $("weight").textContent = percent(f.weight);
  $("close").textContent = number(f.close);
  $("vwap").textContent = number(f.vwap);
  $("opening").textContent = `${number(f.or_high)} / ${number(f.or_low)}`;
  $("entry-price").textContent = number(f.entry_price);
  $("limits").textContent = `${number(f.stop_price)} / ${number(f.target_price)}`;
  $("trades").replaceChildren();
  const trades = scenario.trades.filter((t) => t.exit_ts <= f.timestamp);
  for (const t of trades) {
    const row = document.createElement("tr");
    cell(row, t.direction === "LONG" ? "買い" : "空売り");
    cell(row, `${time(t.entry_ts)} / ${time(t.exit_ts)}`);
    cell(row, number(t.entry_price)); cell(row, number(t.exit_price)); cell(row, percent(t.weight));
    cell(row, yen(t.pnl_yen), t.pnl_yen >= 0 ? "gain" : "loss");
    cell(row, { TARGET: "利確", STOP: "損切り", EOD: "時刻決済" }[t.exit_reason] || t.exit_reason);
    $("trades").append(row);
  }
  if (!trades.length) {
    const row = document.createElement("tr"); cell(row, "決済済み取引なし", "empty"); row.firstChild.colSpan = 7; $("trades").append(row);
  }
  $("events").replaceChildren();
  const events = scenario.frames.slice(0, cursor + 1).filter((frame, i, rows) => i === 0 || frame.reason !== rows[i - 1].reason || frame.signal !== rows[i - 1].signal);
  $("log-count").textContent = `${events.length}件`;
  for (const event of events) {
    const row = document.createElement("div"); row.className = "event";
    const stamp = document.createElement("time"); stamp.textContent = time(event.timestamp);
    const badge = document.createElement("span"); badge.className = "tag"; badge.dataset.signal = event.signal; badge.textContent = labels[event.signal] || event.signal;
    const reason = document.createElement("p"); reason.textContent = reasons[event.reason] || event.reason;
    row.append(stamp, badge, reason); $("events").append(row);
  }
  drawCharts();
}

function chart(id, values, minSpan) {
  const canvas = $(id), width = canvas.clientWidth, height = canvas.clientHeight;
  const scale = window.devicePixelRatio || 1;
  canvas.width = Math.round(width * scale); canvas.height = Math.round(height * scale);
  const ctx = canvas.getContext("2d"); ctx.scale(scale, scale);
  const left = 70, right = width - 22, top = 24, bottom = height - 26;
  const min = Math.min(...values), max = Math.max(...values), margin = Math.max(max - min, minSpan) * .22;
  const low = min - margin, high = max + margin;
  const x = (i) => left + i / (scenario.frames.length - 1) * (right - left);
  const y = (n) => bottom - (n - low) / (high - low) * (bottom - top);
  ctx.clearRect(0, 0, width, height); ctx.font = "11px Segoe UI, sans-serif";
  ctx.lineWidth = 1;
  for (let i = 0; i < 4; i++) {
    const value = low + (high - low) * i / 3, py = y(value);
    ctx.strokeStyle = "#e8eeea"; ctx.beginPath(); ctx.moveTo(left, py); ctx.lineTo(right, py); ctx.stroke();
    ctx.fillStyle = "#6c7971"; ctx.textAlign = "right"; ctx.fillText(number(value), left - 7, py + 3);
  }
  ctx.textAlign = "center";
  for (let i = 0; i < scenario.frames.length; i += 10) ctx.fillText(time(scenario.frames[i].timestamp), x(i), height - 6);
  return { ctx, x, y, left, right };
}

function line(ctx, rows, x, y, key, color) {
  ctx.strokeStyle = color; ctx.lineWidth = 1.7; ctx.beginPath();
  rows.forEach((f, i) => { if (i === 0) ctx.moveTo(x(i), y(f[key])); else ctx.lineTo(x(i), y(f[key])); });
  ctx.stroke();
}

function drawCharts() {
  const rows = scenario.frames.slice(0, cursor + 1);
  const price = chart("price-chart", rows.flatMap((f) => [f.high, f.low, f.vwap]), 8);
  const { ctx, x, y } = price;
  const candleWidth = Math.max(2, Math.min(9, (price.right - price.left) / scenario.frames.length * .55));
  for (const f of rows) {
    const color = f.close >= f.open ? "#208063" : "#c45054";
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x(f.index), y(f.high)); ctx.lineTo(x(f.index), y(f.low)); ctx.stroke();
    ctx.fillRect(x(f.index) - candleWidth / 2, Math.min(y(f.open), y(f.close)), candleWidth, Math.max(1.2, Math.abs(y(f.open) - y(f.close))));
  }
  line(ctx, rows, x, y, "vwap", "#3978ae");
  for (const fill of scenario.fills.filter((event) => event.index <= cursor)) {
    ctx.fillStyle = fill.signal === "ENTRY" ? "#13764e" : "#ad642b";
    ctx.beginPath(); ctx.arc(x(fill.index), y(fill.price), 5, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "white"; ctx.lineWidth = 1.4; ctx.stroke();
  }
  $("price-chart").setAttribute("aria-label", `${time(rows[0].timestamp)}から${time(rows.at(-1).timestamp)}の価格。終値${number(rows.at(-1).close)}`);
  const equity = chart("equity-chart", [model.settings.sim_capital_yen, ...rows.map((f) => f.balance_yen)], 100);
  line(equity.ctx, rows, equity.x, equity.y, "balance_yen", rows.at(-1).pnl_yen < 0 ? "#bc383f" : "#16805b");
}

async function init() {
  try {
    const response = await fetch("/api/demo");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    model = await response.json();
    if (model.allow_live_order !== false || model.order_adapter !== "paper") throw new Error("Invalid paper configuration");
    for (const s of model.scenarios) {
      const button = document.createElement("button"); button.type = "button"; button.setAttribute("role", "tab");
      button.id = `tab-${s.id}`; button.dataset.id = s.id; button.setAttribute("aria-controls", "replay-panel");
      button.textContent = s.title; button.addEventListener("click", () => selectScenario(s.id));
      button.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault(); const i = model.scenarios.indexOf(s), shift = event.key === "ArrowRight" ? 1 : -1;
        const next = model.scenarios[(i + shift + model.scenarios.length) % model.scenarios.length]; selectScenario(next.id); $(`tab-${next.id}`).focus();
      });
      $("scenarios").append(button);
    }
    const s = model.settings;
    const pairs = [["戦略", "寄り付き価格帯ブレイク"], ["寄り付き範囲", `${s.opening_range_minutes}分`], ["手数料 / 片道", percent(s.fee_rate)], ["スリッページ", percent(s.slippage_rate)], ["スプレッド", percent(s.spread_rate)], ["リスク / 上限比率", `${percent(s.risk_per_trade_rate)} / ${percent(s.max_position_w)}`], ["利確倍率", `${s.take_profit_r}R`], ["取引上限", `${s.max_trades_per_day}件 / 日`], ["新規締切 / 手仕舞い", `${s.no_new_entry_time} / ${s.force_flat_time}`]];
    for (const [key, value] of pairs) { const row = document.createElement("div"), dt = document.createElement("dt"), dd = document.createElement("dd"); dt.textContent = key; dd.textContent = value; row.append(dt, dd); $("settings").append(row); }
    $("fingerprint").textContent = `SHA-256 ${model.fingerprint.slice(0, 16)}`;
    $("restart").addEventListener("click", () => { cursor = 0; play(); render(); });
    $("toggle").addEventListener("click", () => { if (timer) pause(); else play(); render(); });
    $("step").addEventListener("click", () => { pause(); cursor = Math.min(cursor + 1, scenario.frames.length - 1); render(); });
    $("cursor").addEventListener("input", (event) => { pause(); cursor = Number(event.target.value); render(); });
    $("speed").addEventListener("change", () => { if (timer) play(); });
    window.addEventListener("resize", drawCharts);
    $("loading").hidden = true; $("workspace").hidden = false; selectScenario("trend");
  } catch (error) {
    $("loading").hidden = true; $("error").hidden = false;
    $("error").textContent = `デモを読み込めませんでした。ページを再読み込みしてください。 ${error.message}`;
  }
}

init();
