const app = document.getElementById("app");
const escapeHTML = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const n = (value, digits = 2) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : Number(value).toLocaleString("en-US", {
        minimumFractionDigits: digits,
        maximumFractionDigits: digits,
      });
const price = (value) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : Number(value).toLocaleString("en-US", {
        maximumFractionDigits:
          Number(value) >= 1000 ? 1 : Number(value) >= 1 ? 3 : 6,
      });
const change = (value, digits = 1) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : `${Number(value) >= 0 ? "+" : ""}${n(value, digits)}`;
const direction = (value) =>
  value == null ? "" : Number(value) >= 0 ? "positive" : "negative";
const dateTime = (value) =>
  value
    ? new Date(value * 1000).toLocaleString("fa-IR", {
        dateStyle: "short",
        timeStyle: "short",
      })
    : "—";
const date = (value) =>
  value
    ? new Date(value * 1000).toLocaleDateString("fa-IR", {
        month: "short",
        day: "numeric",
      })
    : "—";
const baseSymbol = (symbol) =>
  symbol.endsWith("USDT")
    ? symbol.slice(0, -4)
    : symbol.endsWith("TMN")
      ? symbol.slice(0, -3)
      : symbol;
const quoteSymbol = (symbol) => (symbol.endsWith("TMN") ? "TMN" : "USDT");
const symbolName = (symbol) => escapeHTML(baseSymbol(symbol || ""));
let state = null,
  markets = { markets: {} },
  signals = { signals: {} },
  auto = null,
  pageVersion = 0,
  resolution = "1D",
  marketQuery = "",
  marketSort = "symbol";
async function api(url, options) {
  const response = await fetch(url, options);
  if (response.status === 401) {
    location.assign("/login");
    throw new Error("ورود لازم است");
  }
  let data;
  try {
    data = await response.json();
  } catch (_) {
    data = {};
  }
  if (!response.ok) throw new Error(data.detail || "دریافت اطلاعات انجام نشد.");
  return data;
}
async function post(url, body) {
  return api(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
}
function toast(message) {
  const element = document.getElementById("toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove("show"), 5000);
}
function currentRoute() {
  return (location.hash.replace(/^#\/?/, "") || "overview").split("/");
}
function heading(title, subtitle, actions = "") {
  return `<div class="page-heading"><div><h1>${title}</h1><p>${subtitle}</p></div>${actions ? `<div class="heading-actions">${actions}</div>` : ""}</div>`;
}
function badge(text, kind = "") {
  return `<span class="badge ${kind}">${escapeHTML(text)}</span>`;
}
function side(side) {
  return side === "short" ? "فروش / Short" : "خرید / Long";
}
function chart(values, options = {}) {
  const valid = values.filter(
    (point) => Number.isFinite(Number(point.value)) && point.ts,
  );
  if (!valid.length)
    return '<div class="empty-chart">هنوز سابقه‌ای ثبت نشده است.<br>بعد از اجرای چرخه‌ها، تغییرات اینجا دیده می‌شود.</div>';
  const W = 680,
    H = 170,
    left = 54,
    right = 12,
    top = 13,
    bottom = 27,
    nums = valid.map((x) => Number(x.value));
  let min = Math.min(...nums),
    max = Math.max(...nums);
  const padding = (max - min) * 0.16 || Math.max(Math.abs(max) * 0.01, 1);
  min -= padding;
  max += padding;
  const x = (i) =>
      left + (i * (W - left - right)) / Math.max(valid.length - 1, 1),
    y = (v) => top + ((max - v) * (H - top - bottom)) / (max - min);
  let grid = "";
  for (let i = 0; i < 3; i++) {
    const val = max - ((max - min) * i) / 2,
      yy = y(val);
    grid += `<line class="chart-rule" x1="${left}" y1="${yy}" x2="${W - right}" y2="${yy}"/><text class="chart-label" x="4" y="${yy + 4}">${n(val, 1)}</text>`;
  }
  const points = valid.map((p, i) => `${x(i)},${y(Number(p.value))}`).join(" ");
  const first = valid[0],
    last = valid.at(-1);
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="${escapeHTML(options.label || "نمودار تغییرات حساب")}">${grid}<polyline class="chart-line" points="${points}"/><circle class="chart-point" cx="${x(valid.length - 1)}" cy="${y(Number(last.value))}" r="3.5"/><text class="chart-label" x="${left}" y="${H - 4}">${escapeHTML(date(first.ts))}</text><text class="chart-label" text-anchor="end" x="${W - right}" y="${H - 4}">${escapeHTML(date(last.ts))}</text></svg>`;
}
function candleChart(data) {
  const candles = (data.candles || []).filter((c) =>
    ["open", "high", "low", "close", "volume", "ts"].every((k) =>
      Number.isFinite(Number(c[k])),
    ),
  );
  if (!candles.length)
    return '<div class="empty-chart">کندلی برای این بازه موجود نیست.</div>';
  const W = 760,
    H = 320,
    left = 60,
    right = 12,
    top = 12,
    priceBottom = 225,
    volumeTop = 245,
    volumeBottom = 285;
  const overlayValues = [...(data.sma50 || []), ...(data.sma200 || [])]
    .filter((v) => v != null && Number.isFinite(Number(v)))
    .map(Number);
  let min = Math.min(...candles.map((c) => Number(c.low)), ...overlayValues),
    max = Math.max(...candles.map((c) => Number(c.high)), ...overlayValues);
  const pad = (max - min) * 0.04 || 1;
  min -= pad;
  max += pad;
  const step = (W - left - right) / candles.length,
    x = (i) => left + step * (i + 0.5),
    y = (v) => top + ((max - Number(v)) * (priceBottom - top)) / (max - min);
  const maxVolume = Math.max(...candles.map((c) => Number(c.volume)), 1);
  let markup = "";
  for (let i = 0; i < 4; i++) {
    const value = max - ((max - min) * i) / 3,
      yy = y(value);
    markup += `<line class="chart-rule" x1="${left}" y1="${yy}" x2="${W - right}" y2="${yy}"/><text class="chart-label" x="3" y="${yy + 4}">${price(value)}</text>`;
  }
  candles.forEach((c, i) => {
    const up = Number(c.close) >= Number(c.open),
      cx = x(i),
      yo = y(c.open),
      yc = y(c.close),
      high = y(c.high),
      low = y(c.low),
      width = Math.max(1, step * 0.58),
      vh = (Number(c.volume) / maxVolume) * (volumeBottom - volumeTop);
    markup += `<g class="${up ? "candle-up" : "candle-down"}"><title>${escapeHTML(dateTime(c.ts))} · O ${price(c.open)} · H ${price(c.high)} · L ${price(c.low)} · C ${price(c.close)} · V ${price(c.volume)}</title><line x1="${cx}" x2="${cx}" y1="${high}" y2="${low}"/><rect x="${cx - width / 2}" y="${Math.min(yo, yc)}" width="${width}" height="${Math.max(1, Math.abs(yc - yo))}"/><rect class="volume" x="${cx - width / 2}" y="${volumeBottom - vh}" width="${width}" height="${vh}"/></g>`;
  });
  const line = (values, klass) => {
    let segment = "";
    (values || []).slice(-candles.length).forEach((v, i) => {
      if (v != null && Number.isFinite(Number(v)))
        segment += `${x(i)},${y(v)} `;
    });
    return segment ? `<polyline class="${klass}" points="${segment}"/>` : "";
  };
  markup += line(data.sma50, "sma50") + line(data.sma200, "sma200");
  markup += `<text class="chart-label" x="${left}" y="${H - 5}">${escapeHTML(date(candles[0].ts))}</text><text class="chart-label" text-anchor="end" x="${W - right}" y="${H - 5}">${escapeHTML(date(candles.at(-1).ts))}</text>`;
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="نمودار کندل، میانگین متحرک و حجم">${markup}</svg>`;
}
function decisionReason(item) {
  try {
    const detail = JSON.parse(item.detail);
    const labels = {team_review_pending: "در انتظار تحلیل تیم", team_veto: "تیم با ورود مخالف است", team_check_pass: "بررسی تیم بدون مخالفت کافی", llm_veto: "تحلیلگر ورود را رد کرد", llm_veto_short: "تحلیلگر ورود فروش را رد کرد", max_positions: "سقف موقعیت‌های باز", no_signal: "سیگنالی وجود ندارد", "no signal": "سیگنالی وجود ندارد", btc_bullish_regime: "فیلتر روند بیت‌کوین ورود فروش را رد کرد"};
    if (item.action === "open") return `${detail.path?.includes("short") ? "ورود فروش" : "ورود خرید"} · وثیقه ${n(detail.collateral)} USDT`;
    if (item.action === "close" || item.action === "liquidate") return `${detail.signal || detail.reason || "خروج"} · بازده ${change(detail.return_pct)}%`;
    const reason = detail.why || detail.reason || detail.note;
    const spread = /^spread ([\d.]+)% > ([\d.]+)%$/.exec(reason || "");
    if (spread) return `اختلاف خرید و فروش ${spread[1]}٪ از سقف ${spread[2]}٪ بیشتر بود`;
    return labels[reason] || reason || JSON.stringify(detail);
  } catch (_) {
    return item.detail || "—";
  }
}

async function refreshData() {
  // Account controls should be available even if the external exchange is slow.
  const version = pageVersion;
  let late = false;
  const marketRequest = api("/api/markets")
    .then((data) => {
      markets = data;
      if (
        late &&
        pageVersion === version &&
        document.activeElement?.tagName !== "INPUT"
      )
        render();
      return data;
    })
    .catch(() => null);
  const responses = await Promise.allSettled([
    api("/api/state"),
    api("/api/signals"),
    api("/api/autotrade"),
  ]);
  if (responses[0].status === "fulfilled") state = responses[0].value;
  else throw responses[0].reason;
  if (responses[1].status === "fulfilled") signals = responses[1].value;
  if (responses[2].status === "fulfilled") auto = responses[2].value;
  const result = await Promise.race([
    marketRequest,
    new Promise((resolve) => setTimeout(() => resolve(undefined), 1800)),
  ]);
  late = result === undefined;
  const stamp = markets.ts
    ? `آخرین قیمت بازار: ${dateTime(markets.ts)}`
    : "قیمت بازار در دسترس نیست";
  document.getElementById("updated").textContent = stamp;
}

function marketRows(limit) {
  let rows = Object.entries(markets.markets || {}).filter(([symbol]) =>
    symbol.toLowerCase().includes(marketQuery.toLowerCase()),
  );
  if (marketSort === "change")
    rows.sort((a, b) => (b[1].ch24 || 0) - (a[1].ch24 || 0));
  else rows.sort((a, b) => a[0].localeCompare(b[0]));
  if (limit) rows = rows.slice(0, limit);
  return (
    rows
      .map(([symbol, m]) => {
        const s = signals.signals[symbol] || {};
        return `<tr class="clickable" data-coin="${escapeHTML(symbol)}"><td><a class="symbol" href="#/coin/${encodeURIComponent(symbol)}">${symbolName(symbol)}</a><span class="symbol-name">${escapeHTML(symbol)}</span></td><td class="numeric">${price(m.last)} <small class="unit">${quoteSymbol(symbol)}</small></td><td class="numeric ${direction(m.ch24)}">${change(m.ch24)}%</td>${limit ? "" : `<td class="numeric">${price(m.vol24_quote)} <small class="unit">${quoteSymbol(symbol)}</small></td><td>${s.above_sma200 == null ? "—" : s.above_sma200 ? "بالای میانگین" : "زیر میانگین"}</td><td class="numeric">${s.er == null ? "—" : n(s.er, 2)}</td><td>${s.ai_bias ? badge({ bullish: "صعودی", bearish: "نزولی", neutral: "خنثی" }[s.ai_bias] || s.ai_bias) : "—"}</td>`}</tr>`;
      })
      .join("") ||
    `<tr><td colspan="${limit ? 3 : 7}" class="empty">${marketQuery ? "بازاری با این نام پیدا نشد." : "دادهٔ قیمت فعلاً در دسترس نیست."}</td></tr>`
  );
}
function positionsRows() {
  return (
    state.positions
      .map((p) => {
        const current = markets.markets?.[p.symbol]?.last,
          gross =
            current == null
              ? null
              : (current - p.entry_px) * p.qty * (p.side === "short" ? -1 : 1);
        return `<tr class="clickable" data-coin="${escapeHTML(p.symbol)}"><td><a class="symbol" href="#/coin/${encodeURIComponent(p.symbol)}">${symbolName(p.symbol)}</a><span class="symbol-name">${escapeHTML(side(p.side))}</span></td><td class="numeric">${price(p.entry_px)}</td><td class="numeric">${current == null ? "—" : price(current)}</td><td class="numeric ${direction(gross)}">${gross == null ? "—" : change(gross, 2) + " $"}</td><td class="numeric">${n(p.collateral)} $</td><td class="numeric">${price(p.liq_px)}</td><td class="numeric">${current == null ? "—" : n(((p.side === "short" ? p.liq_px - current : current - p.liq_px) / current) * 100, 1) + "%"}</td></tr>`;
      })
      .join("") ||
    '<tr><td colspan="7" class="empty">در حال حاضر موقعیت بازی وجود ندارد.</td></tr>'
  );
}
function decisionRows() {
  return (
    state.decisions
      .slice(0, 5)
      .map(
        (item) =>
          `<tr><td>${dateTime(item.ts)}</td><td><span class="symbol">${escapeHTML(item.symbol)}</span></td><td>${badge({ open: "ورود", close: "خروج", skip: "رد شد", hold: "نگهداری", liquidate: "تسویه", review: "بررسی تیم" }[item.action] || item.action)}<div class="quiet">${escapeHTML(decisionReason(item))}</div></td></tr>`,
      )
      .join("") ||
    '<tr><td colspan="3" class="empty">هنوز تصمیمی ثبت نشده است.</td></tr>'
  );
}
function tradeRows() {
  return (
    state.trades
      .slice(0, 8)
      .map(
        (t) =>
          `<tr><td><span class="symbol">${escapeHTML(t.symbol)}</span></td><td>${escapeHTML(side(t.side))}</td><td class="numeric">${price(t.entry_px)}</td><td class="numeric">${price(t.exit_px)}</td><td class="numeric ${direction(t.return_pct)}">${change(t.return_pct)}%</td><td class="numeric">${n(t.fees)} $</td><td>${escapeHTML(t.close_reason)}</td></tr>`,
      )
      .join("") ||
    '<tr><td colspan="7" class="empty">هنوز معامله‌ای بسته نشده است.</td></tr>'
  );
}
async function overview(version) {
  app.innerHTML =
    heading("نمای حساب", "یک نگاه به وضعیت حساب و مسیر تصمیم‌های آزمایشی") +
    '<div class="loading">در حال دریافت اطلاعات حساب…</div>';
  await refreshData();
  if (version !== pageVersion) return;
  const equity = state.equity.length
    ? state.equity.at(-1).value
    : state.positions.length
      ? null
      : state.cash;
  const profit = equity == null ? null : equity - state.initial_cash;
  const last = state.decisions[0];
  const recentReasons = state.decisions.reduce((acc, item) => {
    try {
      const d = JSON.parse(item.detail);
      if ((d.why || "").startsWith("spread ")) acc.spread++;
      else if (d.why === "btc_bullish_regime") acc.btc++;
      else if (item.action === "hold" && d.note === "no signal") acc.noSignal++;
    } catch (_) {}
    return acc;
  }, {spread: 0, btc: 0, noSignal: 0});
  const running = auto?.running;
  const completed = auto?.last_summary;
  const nextCycle = state.auto_trade && auto?.scheduler_enabled && !state.halted && auto?.last_cycle_ts
    ? auto.last_cycle_ts + (auto.interval_min || 15) * 60 : null;
  app.innerHTML =
    heading(
      "نمای حساب",
      "یک نگاه به وضعیت حساب و مسیر تصمیم‌های آزمایشی",
      `<button class="btn" data-action="halt">${state.halted ? "ادامهٔ حساب" : "توقف حساب"}</button><button class="btn primary" data-action="run" ${running ? "disabled" : ""}>${running ? "چرخه در حال اجراست…" : "اجرای چرخه ↖"}</button>`,
    ) +
    `
  <section class="panel cycle-summary" aria-live="polite"><div><span>وضعیت اجرا</span><strong>${running ? "در حال بررسی بازار" : completed?.status === "error" ? "آخرین اجرا ناموفق" : completed ? "آخرین اجرا پایان یافت" : "هنوز اجرایی ثبت نشده"}</strong><small>${running ? `شروع: ${dateTime(running.started_at)}` : completed ? `پایان: ${dateTime(completed.finished_at)}` : "با اجرای چرخه، وضعیت اینجا نمایش داده می‌شود."}</small></div><div><span>نتیجهٔ آخرین اجرا</span><strong>${completed?.status === "complete" ? `${n(completed.open, 0)} ورود · ${n(completed.closed, 0)} خروج` : "—"}</strong><small>معاملات آزمایشی</small></div><div><span>اجرای خودکار بعدی</span><strong>${nextCycle ? (nextCycle <= Date.now() / 1000 ? "در انتظار اجرا" : dateTime(nextCycle)) : "—"}</strong><small>${state.auto_trade ? (auto?.scheduler_enabled ? "زمان تقریبی" : "زمان‌بند سرور خاموش است") : "چرخهٔ پیوسته خاموش است"}</small></div><div><span>تیم تحلیل</span><strong>${auto?.team_gate == null ? "نامشخص" : auto.team_gate ? "فعال برای ورود" : "غیرفعال"}</strong><small>خروج‌ها مستقل بررسی می‌شوند</small></div></section>
  ${state.decisions.length ? `<section class="panel cycle-reasons"><div><strong>دلیل بررسی‌های اخیر</strong><p>از ${n(state.decisions.length, 0)} تصمیم آخر: ${n(recentReasons.spread, 0)} رد به‌دلیل اسپرد، ${n(recentReasons.noSignal, 0)} بدون سیگنال و ${n(recentReasons.btc, 0)} رد با فیلتر روند بیت‌کوین.</p></div><a href="#/decisions">جزئیات تصمیم‌ها ↖</a></section>` : ""}
  <section class="panel paper-profile"><div><strong>حالت ورود آزمایشی: ${auto?.entry_profile === "active" ? "فعال‌تر" : auto?.entry_profile === "standard" ? "استاندارد" : "نامشخص"}</strong><p>${auto?.entry_profile ? `سقف اسپرد ${n(auto.max_spread_pct, 2)}٪ · حداقل ER ${n(auto.er_min, 2)} · حداکثر ${n(auto.max_entries_7d, 0)} ورود در ۷ روز · فیلتر روند بیت‌کوین برای شورت ${auto.btc_filter_shorts ? "روشن" : "خاموش"}` : "دریافت وضعیت حالت ورود انجام نشد."}</p><p>حالت فعال‌تر می‌تواند تعداد ورود، هزینه و احتمال زیان آزمایشی را افزایش دهد. تغییر از چرخهٔ بعدی اعمال می‌شود.</p></div><button class="btn" data-action="profile" ${auto?.entry_profile ? "" : "disabled"}>${auto?.entry_profile === "active" ? "بازگشت به استاندارد" : "فعال‌تر کردن ورود"}</button></section>
  <div class="overview-grid"><section class="panel balance-panel"><div class="balance-head"><div><div class="control-title">آخرین ارزش ثبت‌شدهٔ حساب</div><strong class="balance-number">${n(equity)}</strong> <span class="unit">USDT</span><div class="balance-sub ${direction(profit)}">${profit == null ? "هنوز ارزش‌گذاری حساب ثبت نشده" : change(profit, 2) + " USDT نسبت به سرمایهٔ اولیه"}</div></div>${badge("آزمایشی")}</div><div class="chart-box">${chart(state.equity)}</div><div class="metric-strip"><div class="metric"><span>موجودی آزاد</span><strong>${n(state.cash)} <small class="unit">USDT</small></strong></div><div class="metric"><span>سرمایهٔ اولیه</span><strong>${n(state.initial_cash)} <small class="unit">USDT</small></strong></div><div class="metric"><span>موقعیت باز</span><strong>${state.positions.length}</strong></div><div class="metric"><span>نرخ برد معاملات بسته</span><strong>${state.stats_all.n ? state.stats_all.win + "%" : "—"}</strong></div></div></section>
  <section class="panel control-panel"><div class="panel-head"><div><h2>وضعیت معامله‌گر</h2><p>قوانین حساب آزمایشی</p></div><span class="status-dot ${state.halted ? "paused" : ""}"></span></div><div class="control-title">چرخهٔ پیوسته</div><div class="control-state">${state.auto_trade ? "روشن" : "خاموش"}</div><p class="control-copy">${state.halted ? "حساب متوقف است؛ ورودهای جدید بررسی نمی‌شوند." : state.auto_trade ? (auto?.scheduler_enabled ? `سیستم هر ${n(auto?.interval_min || 15, 0)} دقیقه شرایط بازار را بررسی می‌کند.` : "تنظیم چرخه روشن است، اما زمان‌بند سرور اجرا نمی‌شود.") : "برای بررسی خودکار شرایط بازار، چرخهٔ پیوسته را روشن کن."}</p><div class="control-details"><div class="detail-row"><span>وضعیت حساب</span><b>${state.halted ? "متوقف" : "فعال"}</b></div><div class="detail-row"><span>آخرین تصمیم</span><b>${last ? dateTime(last.ts) : "هنوز ثبت نشده"}</b></div><div class="detail-row"><span>نوع معامله</span><b>فقط آزمایشی</b></div></div><div class="action-row"><button class="btn" data-action="auto">${state.auto_trade ? "خاموش کردن" : "روشن کردن"} چرخهٔ پیوسته</button></div></section></div>
  <div class="section-head"><h2>موقعیت‌های باز</h2><span class="quiet">سود و زیان ناخالص بر اساس آخرین قیمت</span></div><section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>دارایی</th><th class="numeric">قیمت ورود</th><th class="numeric">آخرین قیمت</th><th class="numeric">سود / زیان</th><th class="numeric">وثیقه</th><th class="numeric">قیمت لیکویید</th><th class="numeric">فاصله تا لیکویید</th></tr></thead><tbody>${positionsRows()}</tbody></table></div></section>
  <div class="section-head"><h2>نبض بازار و تصمیم‌ها</h2><a href="#/markets">همهٔ بازارها ↖</a></div><div class="two-cols"><section class="panel"><div class="panel-head"><div><h2>بازارهای منتخب</h2><p>آخرین دادهٔ دریافت‌شده از والکس</p></div></div><div class="table-wrap"><table class="data-table compact-table"><thead><tr><th>دارایی</th><th class="numeric">قیمت</th><th class="numeric">تغییر ۲۴ ساعت</th></tr></thead><tbody>${marketRows(5)}</tbody></table></div></section><section class="panel"><div class="panel-head"><div><h2>آخرین تصمیم‌ها</h2><p>ثبت ورود، خروج و بررسی شرایط</p></div><a href="#/decisions">همهٔ تصمیم‌ها ↖</a></div><div class="table-wrap"><table class="data-table compact-table"><thead><tr><th>زمان</th><th>نماد</th><th>نتیجه</th></tr></thead><tbody>${decisionRows()}</tbody></table></div></section></div>
  <div class="section-head"><h2>معاملات بسته‌شده</h2><span class="quiet">${state.stats_all.n} معامله</span></div><section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>نماد</th><th>جهت</th><th class="numeric">ورود</th><th class="numeric">خروج</th><th class="numeric">بازده</th><th class="numeric">هزینه</th><th>دلیل</th></tr></thead><tbody>${tradeRows()}</tbody></table></div></section><div class="section-head"><h2>عملکرد مسیرها</h2><span class="quiet">بر اساس معاملات بسته‌شده</span></div><div class="two-cols">${[
    ["قوانین", state.stats_signal],
    ["ورود تحلیلگر", state.stats_llm],
  ]
    .map(
      ([name, stats]) =>
        `<section class="panel"><div class="panel-head"><div><h2>${name}</h2><p>${stats.n} معامله بسته‌شده</p></div></div><div class="detail-row"><span>نرخ برد</span><b>${stats.n ? stats.win + "%" : "—"}</b></div><div class="detail-row"><span>میانگین بازده</span><b>${stats.n ? change(stats.exp, 2) + "%" : "—"}</b></div><div class="detail-row"><span>ضریب سود</span><b>${stats.n ? n(stats.pf) : "—"}</b></div></section>`,
    )
    .join("")}</div>`;
  if (running) setTimeout(() => { if (version === pageVersion && currentRoute()[0] === "overview") render(); }, 4000);
}
async function marketPage(version) {
  app.innerHTML =
    heading("بازارها", "قیمت‌ها، روندها و آخرین نظر تحلیلگر در یک نگاه") +
    '<div class="loading">در حال دریافت بازارها…</div>';
  await refreshData();
  if (version !== pageVersion) return;
  app.innerHTML =
    heading("بازارها", "قیمت‌ها، روندها و آخرین نظر تحلیلگر در یک نگاه") +
    `<div class="toolbar"><input class="search" id="market-search" aria-label="جستجوی بازار" placeholder="جستجوی نماد، مثلاً BTC" value="${escapeHTML(marketQuery)}"><button class="btn" id="sort-market">مرتب‌سازی: ${marketSort === "symbol" ? "نام" : "تغییر ۲۴ ساعت"}</button></div><section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>دارایی</th><th class="numeric">قیمت</th><th class="numeric">تغییر ۲۴ ساعت</th><th class="numeric">حجم ۲۴ ساعت</th><th>روند نسبت به SMA200</th><th class="numeric">ER</th><th>نظر تحلیلگر</th></tr></thead><tbody id="market-rows">${marketRows()}</tbody></table></div></section><p class="quiet">برای دیدن نمودار، یک بازار را انتخاب کن. قیمت‌ها ممکن است در زمان قطعی شبکه به‌روز نشوند.</p>`;
  const search = document.getElementById("market-search");
  search.addEventListener("input", () => {
    marketQuery = search.value;
    document.getElementById("market-rows").innerHTML = marketRows();
  });
  document.getElementById("sort-market").onclick = () => {
    marketSort = marketSort === "symbol" ? "change" : "symbol";
    document.getElementById("market-rows").innerHTML = marketRows();
    document.getElementById("sort-market").textContent =
      "مرتب‌سازی: " + (marketSort === "symbol" ? "نام" : "تغییر ۲۴ ساعت");
  };
}
async function coinPage(version, symbol = "BTCUSDT") {
  app.innerHTML =
    heading(
      escapeHTML(symbol),
      "نمودار و اطلاعات بازار منتخب",
      '<a class="btn" href="#/markets">بازگشت به بازارها</a>',
    ) + '<div class="loading">در حال دریافت نمودار…</div>';
  try {
    const [data, signalData] = await Promise.all([
      api(
        `/api/coin/${encodeURIComponent(symbol)}?res=${encodeURIComponent(resolution)}`,
      ),
      api("/api/signals"),
    ]);
    if (version !== pageVersion) return;
    const s = signalData.signals[symbol] || {},
      a = data.analyst,
      live = data.live;
    app.innerHTML =
      heading(
        escapeHTML(symbol),
        "کندل‌های ذخیره‌شده والکس و آخرین تحلیل",
        '<a class="btn" href="#/markets">بازگشت به بازارها</a>',
      ) +
      `<section class="panel"><div class="panel-head"><div><h2>نمودار قیمت و حجم</h2><p>آخرین قیمت بازار: <span dir="ltr">${live ? price(live.last) : "—"}</span> · دادهٔ نمودار از کندل‌های ذخیره‌شده</p></div><div class="tabs"><button data-res="60" class="${resolution === "60" ? "active" : ""}">1H</button><button data-res="240" class="${resolution === "240" ? "active" : ""}">4H</button><button data-res="1D" class="${resolution === "1D" ? "active" : ""}">1D</button></div></div><div class="chart-box chart-large">${candleChart(data)}</div><div class="chart-legend"><span><i class="legend-up"></i>کندل صعودی</span><span><i class="legend-down"></i>کندل نزولی</span><span><i class="legend-50"></i>SMA 50</span><span><i class="legend-200"></i>SMA 200</span><span>حجم در پایین نمودار</span></div></section><div class="coin-metrics"><div class="metric"><span>شاخص روند ER</span><strong>${data.ind.er == null ? "—" : n(data.ind.er, 2)}</strong></div><div class="metric"><span>نوسان ATR</span><strong>${data.ind.atr_pct == null ? "—" : n(data.ind.atr_pct, 1) + "%"}</strong></div><div class="metric"><span>نسبت به SMA200</span><strong>${s.above_sma200 == null ? "—" : s.above_sma200 ? "بالا" : "پایین"}</strong></div><div class="metric"><span>بازده ۲۰ روز</span><strong class="${direction(s.ret20_pct)}">${s.ret20_pct == null ? "—" : change(s.ret20_pct) + "%"}</strong></div></div><div class="two-cols"><section class="panel"><div class="panel-head"><div><h2>آخرین نظر تحلیلگر</h2><p>${a ? dateTime(a.ts) : "برای این دارایی هنوز نظری ثبت نشده است."}</p></div></div>${a ? `<p>${badge({ bullish: "صعودی", bearish: "نزولی", neutral: "خنثی" }[a.bias] || a.bias)} <span class="quiet">اطمینان ${n(a.confidence * 100, 0)}%</span></p><p class="control-copy">${escapeHTML(a.summary)}</p><p class="quiet">حمایت: ${escapeHTML(a.support || "—")} · مقاومت: ${escapeHTML(a.resistance || "—")}</p>` : '<div class="empty">نظر تحلیلگر پس از ثبت در اینجا نمایش داده می‌شود.</div>'}</section><section class="panel"><div class="panel-head"><div><h2>معاملات این بازار</h2><p>سوابق حساب آزمایشی شما</p></div></div><div class="table-wrap"><table class="data-table compact-table"><thead><tr><th>تاریخ خروج</th><th>جهت</th><th class="numeric">بازده</th></tr></thead><tbody>${
        data.trades
          .slice(0, 6)
          .map(
            (t) =>
              `<tr><td>${dateTime(t.exit_ts)}</td><td>${escapeHTML(side(t.side))}</td><td class="numeric ${direction(t.return_pct)}">${change(t.return_pct)}%</td></tr>`,
          )
          .join("") ||
        '<tr><td colspan="3" class="empty">معامله‌ای ثبت نشده است.</td></tr>'
      }</tbody></table></div></section></div>`;
  } catch (error) {
    if (version !== pageVersion) return;
    app.innerHTML =
      heading(
        escapeHTML(symbol),
        "اطلاعات این بازه در دسترس نیست",
        '<a class="btn" href="#/markets">بازگشت به بازارها</a>',
      ) +
      `<section class="panel"><div class="tabs"><button data-res="60">1H</button><button data-res="240">4H</button><button data-res="1D">1D</button></div><div class="empty">دادهٔ کافی برای این بازه وجود ندارد. بازهٔ دیگری را انتخاب کن.</div></section>`;
  }
}
async function decisionsPage(version) {
  app.innerHTML =
    heading("دفتر تصمیم‌ها", "جزئیات تصمیم‌های حساب آزمایشی") +
    '<div class="loading">در حال دریافت تصمیم‌ها…</div>';
  const data = await api("/api/state");
  if (version !== pageVersion) return;
  app.innerHTML =
    heading(
      "دفتر تصمیم‌ها",
      "جزئیات تصمیم‌های حساب آزمایشی",
      '<a class="btn" href="#/overview">بازگشت به حساب</a>',
    ) +
    `<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>زمان</th><th>نماد</th><th>تصمیم</th><th>جزئیات</th></tr></thead><tbody>${data.decisions.map((item) => `<tr><td>${dateTime(item.ts)}</td><td><span class="symbol">${escapeHTML(item.symbol)}</span></td><td>${badge({ open: "ورود", close: "خروج", skip: "رد شد", hold: "نگهداری", liquidate: "تسویه", review: "بررسی تیم" }[item.action] || item.action)}</td><td class="decision-text" dir="auto">${escapeHTML(decisionReason(item))}</td></tr>`).join("") || '<tr><td colspan="4" class="empty">هنوز تصمیمی ثبت نشده است.</td></tr>'}</tbody></table></div></section>`;
}

async function research(version) {
  app.innerHTML =
    heading("دفتر پژوهش", "مسیر آزمایش‌ها، نتایج و محدودیت‌های استراتژی") +
    '<div class="loading">در حال دریافت دفتر پژوهش…</div>';
  const data = await api("/api/research");
  if (version !== pageVersion) return;
  app.innerHTML =
    heading("دفتر پژوهش", "مسیر آزمایش‌ها، نتایج و محدودیت‌های استراتژی") +
    `<section class="panel"><pre class="notes">${escapeHTML(data.text)}</pre></section>`;
}
async function logs(version) {
  app.innerHTML =
    heading("رویدادها", "اتفاق‌های اخیر حساب و چرخه‌های معامله") +
    '<div class="loading">در حال دریافت رویدادها…</div>';
  const data = await api("/api/logs");
  if (version !== pageVersion) return;
  app.innerHTML =
    heading(
      "رویدادها",
      "اتفاق‌های اخیر حساب و چرخه‌های معامله",
      '<button class="btn" data-action="refresh">به‌روزرسانی</button>',
    ) +
    `<section class="panel"><div class="table-wrap"><table class="data-table"><thead><tr><th>زمان</th><th>نوع</th><th>منبع</th><th>پیام</th></tr></thead><tbody>${data.events.map((e) => `<tr><td>${dateTime(e.ts)}</td><td>${badge({ error: "خطا", warn: "هشدار", info: "رویداد" }[e.level] || e.level, e.level === "error" ? "bad" : "")}</td><td>${escapeHTML(e.source)}</td><td class="decision-text">${escapeHTML(e.message)}${e.detail ? `<div class="quiet">${escapeHTML(e.detail)}</div>` : ""}</td></tr>`).join("") || '<tr><td colspan="4" class="empty">هنوز رویدادی برای این حساب ثبت نشده است.</td></tr>'}</tbody></table></div></section>`;
}
async function settings(version) {
  app.innerHTML =
    heading("تنظیمات", "وضعیت اتصال‌ها و کنترل حساب") +
    '<div class="loading">در حال دریافت تنظیمات…</div>';
  const [s, a] = await Promise.all([
    api("/api/settings"),
    api("/api/autotrade"),
  ]);
  if (version !== pageVersion) return;
  app.innerHTML =
    heading("تنظیمات", "وضعیت اتصال‌ها و کنترل حساب") +
    `<div class="settings-grid"><div><h2>حساب آزمایشی</h2><p>کنترل حساب مستقل شما و اجرای دوره‌ای معاملات کاغذی.</p></div><section class="panel"><div class="setting-row"><span>وضعیت حساب</span><b>${s.halted ? "متوقف" : "فعال"}</b></div><div class="setting-row"><span>چرخهٔ پیوسته</span><b>${a.on ? "روشن" : "خاموش"}</b></div><div class="setting-row"><span>فاصلهٔ بررسی</span><b>${n(a.interval_min, 0)} دقیقه</b></div><div class="setting-row"><button class="btn" data-action="auto">${a.on ? "خاموش کردن" : "روشن کردن"} چرخه</button><button class="btn ${s.halted ? "" : "danger"}" data-action="halt">${s.halted ? "ادامهٔ حساب" : "توقف حساب"}</button></div></section></div><div class="settings-grid"><div><h2>اتصال‌ها</h2><p>وضعیت پیکربندی سرویس‌ها. اسرار نمایش داده نمی‌شوند.</p></div><section class="panel"><div class="setting-row"><span>تحلیلگر هوش مصنوعی</span>${badge(s.llm ? "پیکربندی‌شده" : "پیکربندی نشده", s.llm ? "good" : "")}</div><div class="setting-row"><span>تلگرام</span>${s.owner && s.telegram ? '<button class="btn" data-action="notify">تست اتصال</button>' : ""}${badge(s.telegram ? "پیکربندی‌شده" : "پیکربندی نشده", s.telegram ? "good" : "")}</div><div class="setting-row"><span>ورود حساب</span>${badge(s.dashboard_login ? "فعال" : "تنظیم نشده", s.dashboard_login ? "good" : "")}</div></section></div><div class="settings-grid"><div><h2>دربارهٔ این حساب</h2></div><section class="panel"><p>این حساب فقط برای معاملهٔ آزمایشی است. موجودی فرضی است و هیچ سفارش واقعی به صرافی ارسال نمی‌شود.</p></section></div>`;
}
async function teamPage(version) {
  const job = await api("/api/agent-team");
  if (version !== pageVersion) return;
  const symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "ADAUSDT", "LINKUSDT", "DOTUSDT", "DOGEUSDT"];
  const selected = job.result?.symbol || job.symbol || "BTCUSDT";
  const options = symbols.map((s) => `<option value="${s}" ${s === selected ? "selected" : ""}>${s}</option>`).join("");
  const running = job.status === "running";
  let body = '<div class="empty">هنوز تحلیل تیمی اجرا نشده است.</div>';
  if (running) body = '<div class="empty" role="status">تیم در حال بررسی کندل‌های ذخیره‌شده است. این کار ممکن است چند دقیقه طول بکشد.</div>';
  if (job.status === "error") body = `<div class="empty">${escapeHTML(job.detail)}</div>`;
  if (job.status === "complete") {
    const result = job.result;
    const labels = {bullish: "صعودی", bearish: "نزولی", neutral: "خنثی", unknown: "نامشخص"};
    body = `<div class="team-meta"><span>${escapeHTML(result.symbol)}</span><span>آخرین کندل: ${dateTime(result.candle_ts)}</span><span>زمان تحلیل: ${dateTime(result.created_at)}</span></div><div class="team-reports">${result.reports.map((r) => `<article class="team-report"><div><strong>${escapeHTML(r.name)}</strong> ${badge(r.status === "ok" ? labels[r.stance] || "نامشخص" : "ناموفق", r.status === "error" ? "bad" : "")}</div><p>${escapeHTML(r.summary)}</p></article>`).join("")}</div>`;
  }
  app.innerHTML = heading("تیم تحلیل", "۱۰ نگاه مستقل و یک جمع‌بندی بر اساس کندل‌های ذخیره‌شدهٔ والکس") +
    `<section class="panel team-controls"><div><label for="team-symbol">نماد</label><select id="team-symbol" ${running ? "disabled" : ""}>${options}</select><button class="btn" data-action="team-run" ${running ? "disabled" : ""}>${running ? "در حال تحلیل…" : "اجرای تحلیل تیمی"}</button></div><p>تیم هنگام بررسی ورود آزمایشی نیز اجرا می‌شود و می‌تواند ورود را رد کند. خروج‌ها مستقل‌اند. هر اجرا اعتبار API مصرف می‌کند؛ زمان آخرین کندل را بررسی کنید.</p></section><section class="panel team-results"><div class="panel-head"><h2>گزارش تیم</h2></div>${body}</section>`;
  if (running) setTimeout(() => { if (version === pageVersion && currentRoute()[0] === "team") render(); }, 4000);
}

async function render() {
  const parts = currentRoute(),
    name = parts[0],
    version = ++pageVersion;
  document
    .querySelectorAll("[data-route]")
    .forEach((link) =>
      link.classList.toggle(
        "active",
        link.dataset.route === (name === "coin" ? "markets" : name),
      ),
    );
  try {
    if (name === "markets") await marketPage(version);
    else if (name === "coin") await coinPage(version, parts[1]);
    else if (name === "decisions") await decisionsPage(version);
    else if (name === "research") await research(version);
    else if (name === "team") await teamPage(version);
    else if (name === "logs") await logs(version);
    else if (name === "settings") await settings(version);
    else await overview(version);
  } catch (error) {
    if (version === pageVersion)
      app.innerHTML =
        heading("ارتباط برقرار نشد", "دریافت اطلاعات انجام نشد.") +
        `<section class="panel"><p>${escapeHTML(error.message)}</p><button class="btn" data-action="refresh">تلاش دوباره</button></section>`;
  }
}
app.addEventListener("click", async (event) => {
  const coin = event.target.closest("[data-coin]");
  if (coin) {
    location.hash = "#/coin/" + encodeURIComponent(coin.dataset.coin);
    return;
  }
  const res = event.target.closest("[data-res]");
  if (res) {
    resolution = res.dataset.res;
    render();
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button) return;
  button.disabled = true;
  try {
    const action = button.dataset.action;
    if (action === "refresh") {
      await render();
      return;
    }
    if (action === "team-run") {
      const symbol = document.getElementById("team-symbol").value;
      await post("/api/agent-team", { symbol });
      await render();
      return;
    }
    if (action === "run") {
      const result = await post("/api/run");
      toast(
        result.started
          ? "چرخه شروع شد؛ نتیجه در تصمیم‌ها ثبت می‌شود."
          : result.why || "چرخه آغاز نشد.",
      );
      await render();
    }
    if (action === "halt") {
      const status = await api("/api/settings");
      await post(status.halted ? "/api/resume" : "/api/halt");
      toast(status.halted ? "حساب ادامه یافت." : "حساب متوقف شد.");
    }
    if (action === "notify") {
      const result = await post("/api/notify-test");
      toast(result.sent ? "پیام آزمایشی ارسال شد." : "ارسال پیام انجام نشد.");
    }
    if (action === "auto") {
      const status = await api("/api/autotrade");
      await post("/api/autotrade", { on: !status.on });
      toast(status.on ? "چرخهٔ پیوسته خاموش شد." : "چرخهٔ پیوسته روشن شد.");
    }
    if (action === "profile") {
      const status = await api("/api/autotrade");
      const profile = status.entry_profile === "active" ? "standard" : "active";
      await post("/api/entry-profile", {profile});
      toast(profile === "active" ? "حالت فعال‌تر از چرخهٔ بعدی اعمال می‌شود." : "حالت استاندارد از چرخهٔ بعدی اعمال می‌شود.");
    }
    if (action !== "run") await render();
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
});
document.getElementById("logout").addEventListener("click", async () => {
  try {
    await post("/api/logout");
    location.assign("/");
  } catch (error) {
    toast(error.message);
  }
});
window.addEventListener("hashchange", render);
api("/api/session")
  .then(
    (data) => (document.getElementById("username").textContent = data.username),
  )
  .catch(() => {});
render();
setInterval(() => {
  const name = currentRoute()[0];
  if (
    (name === "overview" || name === "markets") &&
    document.activeElement?.tagName !== "INPUT" &&
    !document.hidden
  )
    render();
}, 60000);
