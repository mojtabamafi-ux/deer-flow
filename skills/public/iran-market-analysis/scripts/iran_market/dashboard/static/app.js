/* Iran market analysis dashboard - front end.
 *
 * Pure DOM + Plotly.js (served from /vendor/plotly.min.js, no CDN).
 * All numbers come from /api/analysis; nothing is recomputed here, so the
 * dashboard and the CLI can never disagree.
 */
"use strict";

const state = {
  markets: [],
  symbols: [],
  symbol: null,
  timeframe: "D1",
  limit: 400,
  analysis: null,
  engineFilter: "all",
  overlays: {
    ema20: true, ema50: true, ema200: true, bb: false, supertrend: true,
    psar: false, ichimoku: false, elliott: true, rtm: true, ict: true, act: true, signals: true,
  },
};

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const num = (v, d = 0) => (v === null || v === undefined || Number.isNaN(v) ? "-" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: d }));
const pct = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v) ? "-" : `${v > 0 ? "+" : ""}${Number(v).toFixed(d)}%`);
const dt = (iso) => (iso ? String(iso).slice(0, 10) : "-");

async function api(path) {
  const res = await fetch(path, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) { /* keep statusText */ }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

/* ------------------------------------------------------------------ bootstrap */

async function init() {
  // deep links: /?symbol=46348559193224090&tf=D1&limit=400&market=equity
  const params = new URLSearchParams(location.search);
  const wantedSymbol = params.get("symbol");
  const wantedMarket = params.get("market");
  if (wantedSymbol) state.symbol = wantedSymbol;
  if (params.get("tf")) state.timeframe = params.get("tf");
  if (params.get("limit")) state.limit = Number(params.get("limit"));

  const [health, markets, symbols] = await Promise.all([api("/api/health"), api("/api/markets"), api("/api/symbols")]);
  $("disclaimer").textContent = health.disclaimer;
  state.markets = markets;
  state.symbols = symbols;

  const marketSel = $("market");
  marketSel.appendChild(new Option("همه بازارها", "all"));
  markets.forEach((m) => marketSel.appendChild(new Option(`${m.label} (${m.symbols})`, m.id)));

  const tfSel = $("timeframe");
  health.timeframes.forEach((tf) => tfSel.appendChild(new Option(tf, tf)));
  tfSel.value = state.timeframe;

  const limitSel = $("limit");
  if ([...limitSel.options].some((o) => Number(o.value) === state.limit)) limitSel.value = String(state.limit);

  buildChips();
  buildEngineFilters();
  renderSourceBadges(health.health_cache);

  marketSel.addEventListener("change", () => fillSymbols(marketSel.value));
  $("search").addEventListener("input", () => fillSymbols(marketSel.value, $("search").value));
  $("symbol").addEventListener("change", () => { state.symbol = $("symbol").value; load(); });
  tfSel.addEventListener("change", () => { state.timeframe = tfSel.value; load(); });
  $("limit").addEventListener("change", () => { state.limit = Number($("limit").value); load(); });
  $("refresh").addEventListener("click", load);

  if (wantedMarket && markets.some((m) => m.id === wantedMarket)) {
    marketSel.value = wantedMarket;
    fillSymbols(wantedMarket);
  } else {
    fillSymbols("all");
  }
  if (state.symbol && !state.analysis) await load();
}

function fillSymbols(market, query) {
  const sel = $("symbol");
  const items = state.symbols.filter(
    (s) => (!market || market === "all" || s.market === market) &&
      (!query || (s.symbol || "").includes(query) || (s.name || "").includes(query) || String(s.code).includes(query))
  );
  sel.innerHTML = "";
  items.slice(0, 400).forEach((s) => {
    const opt = new Option(`${s.symbol} - ${s.name || ""}`, String(s.code));
    sel.appendChild(opt);
  });
  if (!items.length) return;
  const keep = state.symbol && items.some((s) => String(s.code) === String(state.symbol)) ? state.symbol : String(items[0].code);
  sel.value = keep;
  if (keep !== state.symbol) {
    state.symbol = keep;
    load();
  }
}

function buildChips() {
  const labels = {
    ema20: "EMA20", ema50: "EMA50", ema200: "EMA200", bb: "بولینگر", supertrend: "سوپرتند",
    psar: "SAR", ichimoku: "ایچیموکو", elliott: "الیوت", rtm: "نواحی RTM", ict: "OB/FVG", act: "رنج ACT", signals: "سیگنال‌ها",
  };
  const box = $("overlayChips");
  box.innerHTML = "";
  Object.keys(labels).forEach((key) => {
    const chip = el("span", `chip${state.overlays[key] ? " on" : ""}`, labels[key]);
    chip.onclick = () => {
      state.overlays[key] = !state.overlays[key];
      chip.classList.toggle("on", state.overlays[key]);
      renderChart();
    };
    box.appendChild(chip);
  });
}

function buildEngineFilters() {
  const engines = [
    ["all", "همه"], ["rtm", "RTM"], ["act", "ACT"], ["ict", "ICT"], ["elliott", "الیوت"], ["setup", "ستاپ‌ها"],
  ];
  const box = $("engineFilters");
  box.innerHTML = "";
  engines.forEach(([id, label]) => {
    const chip = el("span", `chip${state.engineFilter === id ? " on" : ""}`, label);
    chip.onclick = () => {
      state.engineFilter = id;
      buildEngineFilters();
      renderSignals();
    };
    box.appendChild(chip);
  });
}

function renderSourceBadges(health) {
  const box = $("sourceBadges");
  box.innerHTML = "";
  const probe = el("button", "btn", "بررسی اتصال منابع");
  probe.onclick = async () => {
    probe.disabled = true;
    probe.textContent = "در حال بررسی…";
    try { renderSourceBadges(await api("/api/sources?force=true")); }
    catch (err) { box.appendChild(el("span", "badge error", `خطا: ${err.message}`)); }
    probe.disabled = false;
    probe.textContent = "بررسی اتصال منابع";
  };
  box.appendChild(probe);
  if (!health || !health.sources) {
    box.appendChild(el("span", "badge", "منابع بررسی نشده‌اند"));
    return;
  }
  health.sources.forEach((s) => {
    const cls = s.reachable ? "live" : "error";
    box.appendChild(el("span", `badge ${cls}`, `${s.label || s.name}: ${s.reachable ? "در دسترس" : "بدون دسترسی"}`));
  });
  box.appendChild(el("span", `badge ${health.effective_mode === "live" ? "live" : "sample"}`, `حالت داده: ${health.effective_mode === "live" ? "زنده" : "نمونه مصنوعی"}`));
}

/* --------------------------------------------------------------------- loader */

async function load() {
  if (!state.symbol) return;
  const btn = $("refresh");
  btn.disabled = true;
  btn.textContent = "در حال تحلیل…";
  try {
    state.analysis = await api(
      `/api/analysis?symbol=${encodeURIComponent(state.symbol)}&tf=${state.timeframe}&limit=${state.limit}`
    );
    renderAll(state.analysis);
  } catch (err) {
    $("banner").classList.remove("hidden");
    $("banner").textContent = `خطا در دریافت تحلیل: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.textContent = "تحلیل دوباره";
  }
}

function renderAll(a) {
  renderBanner(a);
  renderPriceStrip(a);
  renderChart();
  renderRecommendation(a);
  renderSignals();
  renderElliott(a);
  renderRtm(a);
  renderIctAct(a);
  renderSetups(a);
  renderIndicators(a);
  renderFactors(a);
}

function renderBanner(a) {
  const box = $("banner");
  const m = a.meta || {};
  const parts = [];
  if (m.data_mode === "sample") parts.push("داده‌های نمایش‌داده‌شده مصنوعی و قطعی (deterministic) هستند، نه داده واقعی بازار؛ صرفاً برای اجرای آفلاین موتور تحلیل.");
  else if (m.data_mode === "cache") parts.push("داده از حافظه نهان محلی خوانده شده است (اتصال زنده برقرار نشد).");
  if (m.live_error) parts.push(`خطای منبع زنده: ${m.live_error}`);
  if (!parts.length) { box.classList.add("hidden"); return; }
  box.classList.remove("hidden");
  box.textContent = parts.join(" ");
}

function renderPriceStrip(a) {
  const box = $("priceStrip");
  box.innerHTML = "";
  const p = (a.snapshot && a.snapshot.price) || {};
  const t = (a.snapshot && a.snapshot.trend) || {};
  const v = (a.snapshot && a.snapshot.volatility) || {};
  const vol = (a.snapshot && a.snapshot.volume) || {};
  const change = p.change_pct;
  const items = [
    [`${a.meta.name} (${a.meta.symbol})`, num(p.close), change >= 0 ? "up" : "down"],
    ["تغییر", pct(change), change >= 0 ? "up" : "down"],
    ["روند", t.label || "-", t.score >= 3 ? "up" : (t.score <= -3 ? "down" : "")],
    ["ADX", num(t.adx, 1), ""],
    ["RSI(14)", num((a.snapshot.momentum || {}).rsi14, 1), ""],
    ["ATR", num(v.atr14, 0), ""],
    ["حجم", num(vol.volume, 0), ""],
    ["حجم نسبی", num(vol.relative_volume, 2), ""],
    ["کندل‌ها", `${num(a.meta.chart_bars)} / ${num(a.meta.history_bars)}`, ""],
    ["منبع داده", a.meta.data_mode === "sample" ? "نمونه" : a.meta.data_mode, ""],
  ];
  items.forEach(([k, val, cls]) => {
    const card = el("div", "metric");
    card.appendChild(el("div", "k", k));
    card.appendChild(el("div", `v ${cls}`, String(val)));
    box.appendChild(card);
  });
}

/* ---------------------------------------------------------------------- chart */

function renderChart() {
  const a = state.analysis;
  if (!a || !a.chart) return;
  const c = a.chart;
  const o = state.overlays;
  const traces = [];
  const shapes = [];
  const annotations = [];

  traces.push({
    type: "candlestick", name: "قیمت", x: c.dt, open: c.open, high: c.high, low: c.low, close: c.close,
    increasing: { line: { color: "#22c55e" }, fillcolor: "#16653499" },
    decreasing: { line: { color: "#ef4444" }, fillcolor: "#7f1d1d99" },
    yaxis: "y", xaxis: "x",
  });

  const line = (key, name, color, dash, axis = "y") => {
    if (!o[key] || !c[key]) return;
    traces.push({ type: "scatter", mode: "lines", name, x: c.dt, y: c[key], line: { color, width: 1.2, dash }, yaxis: axis, hoverinfo: "name+y" });
  };
  line("ema20", "EMA20", "#38bdf8");
  line("ema50", "EMA50", "#f59e0b");
  line("ema200", "EMA200", "#a78bfa");
  line("bb_upper", "بولینگر بالا", "#64748b", "dot");
  line("bb_lower", "بولینگر پایین", "#64748b", "dot");
  line("supertrend", "سوپرتند", "#22d3ee", "solid");
  line("psar", "SAR", "#f472b6", "dot");
  line("tenkan", "تنکان", "#94a3b8");
  line("kijun", "کیجون", "#cbd5e1");
  line("senkou_a", "ابر A", "#47556988");
  line("senkou_b", "ابر B", "#47556988");

  if (o.rtm && a.rtm && a.rtm.overlay) {
    a.rtm.overlay.forEach((z) => {
      const color = z.kind === "demand" ? "rgba(34,197,94,0.14)" : "rgba(239,68,68,0.14)";
      const border = z.kind === "demand" ? "rgba(34,197,94,0.55)" : "rgba(239,68,68,0.55)";
      shapes.push({ type: "rect", xref: "x", yref: "y", x0: z.x0, x1: z.x1, y0: z.y0, y1: z.y1, fillcolor: color, line: { color: border, width: 1 }, layer: "below" });
      annotations.push({ x: z.x0, y: z.y1, text: `${z.type}${z.ftr ? " •FTR" : ""}`, showarrow: false, font: { size: 9, color: border }, xanchor: "left" });
      shapes.push({ type: "line", xref: "x", yref: "y", x0: z.x0, x1: z.x1, y0: z.eq, y1: z.eq, line: { color: border, width: 0.6, dash: "dot" }, layer: "below" });
    });
  }

  if (o.ict && a.ict) {
    (a.ict.order_blocks || []).forEach((b) => shapes.push({
      type: "rect", xref: "x", yref: "y", x0: b.x0, x1: b.x1, y0: b.y0, y1: b.y1,
      fillcolor: b.kind === "bullish" ? "rgba(56,189,248,0.10)" : "rgba(251,146,60,0.10)",
      line: { color: b.kind === "bullish" ? "rgba(56,189,248,0.5)" : "rgba(251,146,60,0.5)", width: 1, dash: "dash" }, layer: "below",
    }));
    (a.ict.fvg || []).forEach((g) => shapes.push({
      type: "rect", xref: "x", yref: "y", x0: g.x0, x1: g.x1, y0: g.y0, y1: g.y1,
      fillcolor: "rgba(168,85,247,0.10)", line: { color: "rgba(168,85,247,0.45)", width: 1, dash: "dot" }, layer: "below",
    }));
    (a.ict.sweeps || []).forEach((s) => annotations.push({
      x: s.x, y: s.y, text: s.direction > 0 ? "⇧" : "⇩", showarrow: false, font: { size: 12, color: "#facc15" },
    }));
  }

  if (o.act && a.act && a.act.overlay) {
    a.act.overlay.forEach((r) => shapes.push({
      type: "rect", xref: "x", yref: "y", x0: r.x0, x1: r.x1, y0: r.y0, y1: r.y1,
      fillcolor: "rgba(250,204,21,0.08)", line: { color: "rgba(250,204,21,0.5)", width: 1, dash: "dashdot" }, layer: "below",
    }));
  }

  if (o.elliott && a.elliott && a.elliott.overlay) {
    const xs = [];
    const ys = [];
    a.elliott.overlay.lines.forEach((l) => {
      xs.push(l.x0, l.x1, null);
      ys.push(l.y0, l.y1, null);
    });
    traces.push({
      type: "scatter", mode: "lines", name: "الیوت", x: xs, y: ys,
      line: { color: "#fde047", width: 2 }, yaxis: "y", hoverinfo: "skip",
    });
    (a.elliott.overlay.annotations || []).forEach((ann) => annotations.push({
      x: ann.x, y: ann.y, text: ann.text, showarrow: false, yshift: ann.direction > 0 ? -12 : 12,
      font: { size: 11, color: "#fde047" },
      bgcolor: "rgba(11,15,23,0.7)", bordercolor: "#fde047", borderwidth: 1, borderpad: 2,
    }));
  }

  if (o.signals && a.signals) {
    const buy = a.signals.filter((s) => s.direction > 0);
    const sell = a.signals.filter((s) => s.direction < 0);
    const marker = (list, color, name, symbol) => traces.push({
      type: "scatter", mode: "markers", name, xaxis: "x", yaxis: "y",
      x: list.map((s) => s.dt), y: list.map((s) => s.entry),
      marker: { symbol, size: 12, color, line: { color: "#0b0f17", width: 1 } },
      text: list.map((s) => `${s.title || s.setup_name || s.setup}<br>ورود ${num(s.entry)} • استاپ ${num(s.stop)} • هدف ${num(s.target)}`),
      hoverinfo: "text",
    });
    if (buy.length) marker(buy, "#22c55e", "سیگنال خرید", "triangle-up");
    if (sell.length) marker(sell, "#ef4444", "سیگنال فروش", "triangle-down");
  }

  traces.push({
    type: "bar", name: "حجم", x: c.dt, y: c.volume, yaxis: "y2", marker: { color: "#33415588" }, hoverinfo: "y",
  });
  traces.push({ type: "scatter", mode: "lines", name: "RSI", x: c.dt, y: c.rsi14, yaxis: "y3", line: { color: "#38bdf8", width: 1.2 }, hoverinfo: "name+y" });
  shapes.push({ type: "line", xref: "paper", yref: "y3", x0: 0, x1: 1, y0: 70, y1: 70, line: { color: "#ef444455", width: 1, dash: "dot" } });
  shapes.push({ type: "line", xref: "paper", yref: "y3", x0: 0, x1: 1, y0: 30, y1: 30, line: { color: "#22c55e55", width: 1, dash: "dot" } });
  traces.push({ type: "bar", name: "MACD hist", x: c.dt, y: c.macd_hist, yaxis: "y4", marker: { color: c.macd_hist.map((v) => (v >= 0 ? "#22c55e99" : "#ef444499")) }, hoverinfo: "y" });
  traces.push({ type: "scatter", mode: "lines", name: "MACD", x: c.dt, y: c.macd_line, yaxis: "y4", line: { color: "#38bdf8", width: 1.2 } });
  traces.push({ type: "scatter", mode: "lines", name: "Signal", x: c.dt, y: c.macd_signal, yaxis: "y4", line: { color: "#f59e0b", width: 1.2 } });

  const grid = { gridcolor: "#1e293b", zerolinecolor: "#1e293b" };
  const layout = {
    direction: "ltr",
    paper_bgcolor: "#0b0f17", plot_bgcolor: "#0b0f17",
    font: { family: "Vazirmatn, Segoe UI, sans-serif", size: 11, color: "#8fa1bd" },
    margin: { l: 60, r: 20, t: 20, b: 40 },
    showlegend: false,
    hovermode: "x unified",
    dragmode: "pan",
    xaxis: { ...grid, rangeslider: { visible: false }, type: "date" },
    yaxis: { ...grid, domain: [0.46, 1], scaleanchor: undefined, tickformat: ",.0f" },
    yaxis2: { ...grid, domain: [0.33, 0.44], showticklabels: false },
    yaxis3: { ...grid, domain: [0.17, 0.31], range: [0, 100] },
    yaxis4: { ...grid, domain: [0.0, 0.15] },
    shapes, annotations,
  };
  Plotly.react($("chart"), traces, layout, { responsive: true, displaylogo: false, scrollZoom: true });
}

/* --------------------------------------------------------------- panels */

function renderRecommendation(a) {
  const r = a.recommendation || {};
  const box = $("recommendation");
  box.className = `recommendation ${r.action === "buy" ? "buy" : r.action === "sell" ? "sell" : ""}`;
  box.innerHTML = "";
  const head = el("div", "rec-head");
  const action = el("span", `rec-action ${r.action}`, r.action_fa || "-");
  head.appendChild(action);
  head.appendChild(el("span", "muted", `امتیاز هم‌راستایی: ${num(r.score, 1)} از ۱۰۰ • اطمینان: ${num((r.confidence || 0) * 100, 0)}٪`));
  const badge = el("span", "tag", r.action === "wait" ? "بدون ورود" : "سیگنال تحلیلی");
  head.appendChild(badge);
  box.appendChild(head);

  const grid = el("div", "rec-grid");
  [
    ["قیمت فعلی", num(r.price)],
    ["نقطه ورود", num(r.entry)],
    ["حد ضرر", num(r.stop)],
    ["هدف ۱", num(r.target)],
    ["هدف ۲", num(r.target2)],
    ["ریسک به ریوارد", num(r.rr, 2)],
  ].forEach(([k, v]) => {
    const b = el("div", "rec-box");
    b.appendChild(el("div", "k", k));
    b.appendChild(el("div", "v", v));
    grid.appendChild(b);
  });
  box.appendChild(grid);

  if (r.reasons && r.reasons.length) {
    box.appendChild(el("h3", null, "دلایل"));
    const ul = el("ul");
    r.reasons.forEach((t) => ul.appendChild(el("li", null, t)));
    box.appendChild(ul);
  }
  if (r.supporting_signals && r.supporting_signals.length) {
    box.appendChild(el("h3", null, "سیگنال‌های پشتیبان"));
    const ul = el("ul");
    r.supporting_signals.forEach((s) => ul.appendChild(el("li", null, `${s.engine.toUpperCase()} - ${s.title || s.setup} (اطمینان ${num((s.confidence || 0) * 100, 0)}٪، R:R ${num(s.rr, 2)})`)));
    box.appendChild(ul);
  }
  if (r.risks && r.risks.length) {
    box.appendChild(el("h3", null, "ریسک‌ها و محدودیت‌ها"));
    const ul = el("ul");
    r.risks.forEach((t) => ul.appendChild(el("li", null, t)));
    box.appendChild(ul);
  }
  box.appendChild(el("p", "note warn", r.disclaimer || ""));
}

function renderSignals() {
  const a = state.analysis;
  const box = $("signals");
  box.innerHTML = "";
  const all = (a.signals || []).filter((s) => state.engineFilter === "all" || s.engine === state.engineFilter);
  $("signalCount").textContent = `${all.length} سیگنال روی آخرین کندل`;
  if (!all.length) {
    box.appendChild(el("p", "muted", "روی آخرین کندل سیگنال معتبری ثبت نشد. نبود سیگنال، خودش یک نتیجه است: ستاپ با شرایط تعریف‌شده شکل نگرفته است."));
    return;
  }
  all.forEach((s) => {
    const card = el("div", `signal ${s.direction > 0 ? "buy" : "sell"}`);
    const row = el("div", "row");
    const title = el("span", "title", `${s.direction > 0 ? "▲" : "▼"} ${s.title || s.setup_name || s.setup}`);
    row.appendChild(title);
    row.appendChild(el("span", "engine", s.engine.toUpperCase()));
    card.appendChild(row);

    const tags = el("div", "row");
    tags.appendChild(el("span", "tag", `اطمینان ${num((s.confidence || 0) * 100, 0)}٪`));
    if (s.rr) tags.appendChild(el("span", "tag", `R:R ${num(s.rr, 2)}`));
    if (s.confluence) tags.appendChild(el("span", "tag", `هم‌راستایی ${s.confluence}`));
    const m = s.measured || {};
    if (m.trades) {
      tags.appendChild(el("span", `tag ${m.sample_ok ? "good" : "bad"}`, `نرخ پیروزی تاریخی ${num((m.win_rate || 0) * 100, 0)}٪ روی ${m.trades} معامله`));
    }
    card.appendChild(tags);

    const levels = el("div", "levels");
    levels.appendChild(el("span", null, `ورود: ${num(s.entry)}`));
    levels.appendChild(el("span", null, `استاپ: ${num(s.stop)}`));
    levels.appendChild(el("span", null, `هدف: ${num(s.target)}`));
    levels.appendChild(el("span", null, `تاریخ: ${dt(s.dt)}`));
    card.appendChild(levels);

    const detail = el("div", "detail hidden");
    const ul = el("ul");
    (s.reasons || (s.reason ? [s.reason] : [])).forEach((t) => ul.appendChild(el("li", null, t)));
    detail.appendChild(ul);
    detail.appendChild(el("p", "muted", `شناسه: ${s.setup || s.type} • موتور: ${s.engine}${s.zone_id ? ` • ناحیه: ${s.zone_id}` : ""}`));
    card.appendChild(detail);
    card.onclick = () => detail.classList.toggle("hidden");
    box.appendChild(card);
  });
}

function renderElliott(a) {
  const e = a.elliott || {};
  $("elliottSummary").textContent = e.summary || "شمارش موجی پیدا نشد.";
  $("elliottScore").textContent = e.threshold_pct ? `آستانه زیگزاگ: ${num(e.threshold_pct, 2)}٪` : "";
  const wrap = $("elliottWaves");
  wrap.innerHTML = "";
  const waves = (e.waves || []).filter((w) => w.degree !== "minor");
  if (!waves.length) {
    wrap.appendChild(el("p", "muted", "موج معتبری شناسایی نشد."));
  } else {
    const table = el("table");
    table.appendChild(head(["موج", "از", "به", "طول", "کندل", "درصد انگیزش", "بازگشت موج قبل", "وضعیت"]));
    waves.forEach((w) => {
      table.appendChild(row([
        w.label, num(w.start_price), num(w.end_price), num(w.length), num(w.bars),
        w.pct_of_impulse === null ? "-" : `${num(w.pct_of_impulse, 1)}٪`,
        w.retrace_of_previous === null ? "-" : num(w.retrace_of_previous, 2),
        w.provisional ? "در حال شکل‌گیری" : "تکمیل‌شده",
      ]));
    });
    wrap.appendChild(table);
  }

  const patterns = $("elliottPatterns");
  patterns.innerHTML = "";
  (e.patterns || []).forEach((p) => {
    patterns.appendChild(el("p", null, `${p.label} — اطمینان ${num((p.confidence || 0) * 100, 0)}٪ (${dt((e.pivots || [])[0] && p.start_index !== undefined ? null : null)})`));
  });
  if (!(e.patterns || []).length) patterns.appendChild(el("p", "muted", "الگویی ثبت نشد."));

  const targets = $("elliottTargets");
  targets.innerHTML = "";
  const cur = e.current;
  if (!cur) {
    targets.appendChild(el("p", "muted", "-"));
    return;
  }
  const table = el("table");
  table.appendChild(head(["هدف", "قیمت", "نسبت"]));
  (cur.targets || []).slice(0, 6).forEach((t) => table.appendChild(row([t.label, num(t.price), num(t.ratio, 3)])));
  targets.appendChild(table);
  targets.appendChild(el("p", "note", `موج جاری: ${cur.last_label} • ${cur.in_progress ? "در حال تکمیل" : "تکمیل‌شده"} • مرحله بعد: ${cur.next_label || "-"} • سطح بی‌اعتباری: ${num(cur.invalidation)}`));
}

function renderRtm(a) {
  const r = a.rtm || {};
  const s = r.summary || {};
  $("rtmSummary").textContent = `${num(s.zones_total)} ناحیه • ${num(s.zones_live)} فعال • ${num(s.zones_mitigated)} شکسته‌شده`;
  const wrap = $("rtmZones");
  wrap.innerHTML = "";
  const zones = (r.active || []).slice(0, 40);
  if (!zones.length) {
    wrap.appendChild(el("p", "muted", "ناحیه فعالی باقی نمانده است (همه نواحی شکسته یا مصرف شده‌اند)."));
    return;
  }
  const table = el("table");
  table.appendChild(head(["نوع", "کف", "سقف", "EQ", "MPL", "طلایی", "قدرت", "خروج (ATR)", "برخوردها", "وضعیت"]));
  zones.forEach((z) => {
    table.appendChild(row([
      `${z.kind_fa} ${z.type}${z.ftr ? " •FTR" : ""}${z.compression ? " •فشردگی" : ""}`,
      num(z.low), num(z.high), num(z.eq), num(z.mpl), num(z.golden),
      num(z.strength, 2), num(z.departure_atr, 1), num(z.touches), z.status_fa,
    ]));
  });
  wrap.appendChild(table);
}

function renderIctAct(a) {
  const box = $("ictPanel");
  box.innerHTML = "";
  const ict = a.ict || {};
  const d = ict.dealing_range || {};
  const table = el("table");
  table.appendChild(head(["مورد", "مقدار"]));
  table.appendChild(row(["ساختار بازار", (a.structure || {}).structure || "-"]));
  table.appendChild(row(["آخرین BOS", (a.structure || {}).last_bos ? `${(a.structure || {}).last_bos.type} @ ${num((a.structure || {}).last_bos.level)} (${dt((a.structure || {}).last_bos.dt)})` : "-"]));
  table.appendChild(row(["آخرین CHoCH", (a.structure || {}).last_choch ? `${(a.structure || {}).last_choch.type} @ ${num((a.structure || {}).last_choch.level)} (${dt((a.structure || {}).last_choch.dt)})` : "-"]));
  table.appendChild(row(["اوردر بلاک زنده", num((ict.order_blocks || []).length)]));
  table.appendChild(row(["FVG زنده", num((ict.fvg || []).length)]));
  table.appendChild(row(["جمع‌آوری نقدشوندگی", num((ict.sweeps || []).length)]));
  table.appendChild(row(["محدوده معامله", d.high ? `${num(d.low)} تا ${num(d.high)}` : "-"]));
  table.appendChild(row(["تعادل / OTE", d.equilibrium ? `${num(d.equilibrium)} | ${num(d.ote_low)}-${num(d.ote_high)}` : "-"]));
  table.appendChild(row(["موقعیت قیمت", d.zone_fa || "-"]));
  box.appendChild(table);

  const act = $("actPanel");
  act.innerHTML = "";
  const phases = [["long", "سمت خرید"], ["short", "سمت فروش"]];
  const t = el("table");
  t.appendChild(head(["سمت", "فاز", "رنج", "ATR رنج", "ADX", "حجم نسبی", "فشردگی", "شکست"]));
  phases.forEach(([key, label]) => {
    const st = (a.act || {})[key] || {};
    t.appendChild(row([
      label, st.phase_fa || "-",
      st.range_low ? `${num(st.range_low)} - ${num(st.range_high)}` : "-",
      num(st.range_atr, 2), num(st.adx, 1), num(st.relative_volume, 2),
      st.compression ? "بله" : "خیر", st.breakout ? "بله" : "خیر",
    ]));
  });
  act.appendChild(t);
  act.appendChild(el("p", "note", "فاز «انباشت» به‌تنهایی سیگنال نیست؛ سیگنال وقتی صادر می‌شود که فشردگی تأیید و شکست انجام شود."));
}

function renderSetups(a) {
  const wrap = $("setupRanking");
  wrap.innerHTML = "";
  const ranking = (a.setups || {}).ranking || [];
  if (!ranking.length) {
    wrap.appendChild(el("p", "muted", "معامله‌ای برای اندازه‌گیری ثبت نشد."));
  } else {
    const table = el("table");
    table.appendChild(head(["ستاپ", "معاملات", "نرخ پیروزی", "امید ریاضی (R)", "ضریب سود", "بیشترین افت (R)", "میانگین ماندگاری", "درجه"]));
    ranking.forEach((s) => {
      table.appendChild(row([
        s.name, num(s.trades),
        s.win_rate === null ? "-" : `${num(s.win_rate * 100, 1)}٪`,
        num(s.expectancy_r, 3), s.profit_factor === null ? "-" : num(s.profit_factor, 2),
        num(s.max_drawdown_r, 2), num(s.avg_bars, 1), s.grade,
      ]));
    });
    wrap.appendChild(table);
  }
  const o = (a.backtest || {}).overall || {};
  $("backtestNote").textContent =
    `کل معاملات شبیه‌سازی‌شده: ${num(o.trades)} • نرخ پیروزی ${o.win_rate === null ? "-" : num(o.win_rate * 100, 1) + "٪"} • ` +
    `امید ریاضی ${num(o.expectancy_r, 3)}R • ضریب سود ${o.profit_factor === null ? "-" : num(o.profit_factor, 2)} • بیشترین افت ${num(o.max_drawdown_r, 2)}R. ` +
    "این اعداد صرفاً نتیجه گذشته روی همین داده‌ها هستند و هیچ تضمینی برای آینده ایجاد نمی‌کنند. درجه «نمونه کم» یعنی تعداد معاملات برای نتیجه‌گیری آماری کافی نیست.";
}

function renderIndicators(a) {
  const box = $("indicators");
  box.innerHTML = "";
  const s = a.snapshot || {};
  const groups = [
    ["روند", s.trend, { adx: 1, plus_di: 1, minus_di: 1 }],
    ["مومنتوم", s.momentum, { rsi14: 1, rsi6: 1, stoch_k: 1, cci20: 1, williams_r: 1, roc10: 1 }],
    ["نوسان", s.volatility, { atr14: 0, atr_pct: 2, bb_bandwidth: 1, bb_percent_b: 2, hv20: 1 }],
    ["حجم", s.volume, { volume: 0, volume_sma20: 0, relative_volume: 2, mfi14: 1, cmf20: 3 }],
  ];
  groups.forEach(([title, data, digits]) => {
    if (!data) return;
    Object.entries(data).forEach(([k, v]) => {
      if (typeof v === "boolean" || v === null || typeof v === "string") return;
      const card = el("div", "ind");
      card.appendChild(el("div", "k", `${title} • ${k}`));
      card.appendChild(el("div", "v", num(v, digits[k] === undefined ? 2 : digits[k])));
      box.appendChild(card);
    });
  });
}

function renderFactors(a) {
  const wrap = $("factors");
  wrap.innerHTML = "";
  const rec = a.recommendation || {};
  const conf = rec.confluence || {};
  $("confluenceScore").textContent = `امتیاز کل ${num(conf.score, 1)} • صعودی ${num(conf.bullish_factors)} • نزولی ${num(conf.bearish_factors)} • خنثی ${num(conf.neutral_factors)}`;
  const factors = rec.key_factors || [];
  if (!factors.length) {
    wrap.appendChild(el("p", "muted", "-"));
    return;
  }
  const table = el("table");
  table.appendChild(head(["عامل", "مقدار", "امتیاز", "توضیح"]));
  factors.forEach((f) => {
    table.appendChild(row([f.name, String(f.value ?? "-"), num(f.score, 2), f.note]));
  });
  wrap.appendChild(table);
}

/* ------------------------------------------------------------------ helpers */

function head(cols) {
  const tr = el("tr");
  cols.forEach((c) => tr.appendChild(el("th", null, c)));
  return tr;
}
function row(cells) {
  const tr = el("tr");
  cells.forEach((c) => {
    const td = el("td", typeof c === "number" ? "num" : null, String(c));
    tr.appendChild(td);
  });
  return tr;
}

init().catch((err) => {
  const box = $("banner");
  box.classList.remove("hidden");
  box.textContent = `راه‌اندازی داشبورد ناموفق بود: ${err.message}`;
});
