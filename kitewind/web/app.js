// Kite Wind dashboard. Draws the forecast JSON with plain SVG; no libraries. English and Spanish.
"use strict";

const TZ = "Europe/Madrid";
const HOUR = 3600e3;
const STALE_MS = 45 * 60e3;  // the forecast is published every ~10 min

// ---------------------------------------------------------------- languages

const f1 = (x) => (x == null ? "–" : fmtNum(+x, 1));
const pct = (p) => `${Math.round(p * 100)}%`;

const I18N = {
  en: {
    locale: "en-GB",
    status: { go: ["GO", "Go"], caution: ["MARGINAL", "Marginal"], nogo: ["NO-GO", "No-go"], night: ["NIGHT", "Night"], nodata: ["NO DATA", "No data"] },
    compass: ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"],
    html: {
      next3: "Next 3 days",
      windTitle: "Wind across 10–150 m", windHint: "line: best model · band: likely range (10–90 % of ensemble runs)",
      probTitle: "Chance of enough wind", probHint: "share of ensemble runs at or above the minimum",
      heightsTitle: "Wind at each height", heightsHint: "best model · grey: below the minimum",
      rainTitle: "Rain", bestModel: "best model", rainProbTitle: "Chance of rain", stormProbTitle: "Chance of thunderstorm",
      sources: "Where this comes from",
    },
    updated: (hm, age) => `Updated ${hm} (${age})`, refreshes: "refreshes every 5 min",
    justNow: "just now", minAgo: (n) => `${n} min ago`, hAgo: (n) => `${n} h ago`,
    stale: (age) => `<b>This forecast was made ${age}.</b> The computer that calculates it may be offline; check the times below before deciding.`,
    testing: "<b>Windy Testing key in use for decisions.</b> Windy shuffles Testing-plan data on purpose.",
    demo: "<b>Demo data.</b> These forecasts are made up so the page can be tried without any API.",
    cannotReach: "Cannot reach the forecast: ",
    site: (s) => s.lat == null ? s.name : `${s.name} · ${s.lat.toFixed(4)}, ${s.lon.toFixed(4)} · ground ${s.elevation_m} m`,
    today: "today", tomorrow: "tomorrow", Today: "Today", Tomorrow: "Tomorrow",
    goHours: (n) => `<b>${n} h GO</b>`, margHours: (n) => `${n} h marginal`, noGoHours: "No GO hours",
    now: "Now", noData: "No forecast covers this hour yet", sunDown: "Sun is down",
    allMet: (w) => `All limits met · ${w} m/s across 10–150 m`,
    noWindow: (d) => `<b>No GO hours</b> in the next ${d} days.`,
    goNow: (b, n) => `<b>GO now</b> until <b>${b}</b> (${n} h).`,
    nextWindow: (day, a, b, n) => `Next GO window: <b>${day} ${a}</b> – <b>${b}</b> (${n} h).`,
    more: (n) => `${n} more window${n > 1 ? "s" : ""} later.`,
    tile: {
      wind: "Wind 10–150 m", likely: (a, b, m) => `likely ${a}–${b} · needs ≥ ${m}`,
      chance: "Chance of enough wind", runs: (n, m) => `of ${n} ensemble runs reach ${m} m/s`, noEns: "no ensemble data",
      rain: "Rain", none: "None", chanceOf: (p) => `${p} chance`, mustZero: "must be zero",
      storm: "Thunderstorm", forecast: "Forecast", possible: "Possible",
    },
    tip: {
      wind: "Wind 10–150 m", likely: "Likely range (10–90 %)", spread: "Spread of forecasts",
      chance: (m) => `Chance of ≥ ${m} m/s`, at150: "At 150 m", from: "From", rain: "Rain", chance2: "chance",
      storm: "Thunderstorm", yes: "forecast", no: "no", vis: "Visibility", temp: "Temperature", by: "Decided by",
    },
    chart: {
      status: "Status", dir: "dir", now: "Now", min: (m) => `${m} m/s minimum`, strong: (m) => `${m} m/s strong`,
      needed: (p) => `${p} needed`, nogo: (v) => `${v} no-go`, notProvided: "not provided by this model", noEns: "no ensemble data",
    },
    band: "Likely range",
    src: {
      head: ["Model", "Grid", "Runs", "Updated", "Until", "Step", "10–150 m from", "Status"],
      group: { "Open-Meteo": "Open-Meteo · decides", Windy: "Windy", "Open-Meteo ensemble": "Ensembles · likely range and chances" },
      ok: "OK", waiting: "waiting", noKey: "no key", shownOnly: "shown only: Testing key", never: "never",
      runs: (n) => `${n} runs`, points: (a, b) => `${a}/${b} points`,
    },
    hf: {
      none: () => "–", levels: () => "10 m + pressure levels", law: () => "10 m + 1/7 law",
      measured: (a, b, e) => `measured ${a}–${b} m${e ? " + extended" : ""}`, shear: (x) => `10 m + shear from ${x}`,
    },
    fine: (L, d, windy) =>
      `Each hour is decided by the finest model that covers it (AROME HD, 1.3 km, first); the next ones fill in what it ` +
      `lacks, such as storm codes. Wind is the height-averaged mean over ${d.heights[0]}–${d.heights[d.heights.length - 1]} m ` +
      `above the ground. The likely range and the chances come from four ensembles, each counted equally. ` +
      `Limits: mean wind ≥ ${L.min_wind} m/s; no rain (≥ ${L.rain_nogo} mm/h is no-go); no thunderstorm; ` +
      `fog only below ${L.visibility_nogo} m. Gusts are not used. ` +
      (windy && !d.windy_used ? "Windy data is shown for comparison and decides nothing while its key is a Testing key. " : "") +
      `Data: Open-Meteo (CC BY 4.0)${windy ? " and the Windy Point Forecast API" : ""}.`,
    reasons: {
      wind_low: (p) => `Not enough wind: ${f1(p.w)} m/s, needs ${f1(p.min)}`,
      wind_prob: (p) => `Chance of ${f1(p.min)} m/s or more: only ${pct(p.p)}`,
      wind_disagree: (p) => `Forecasts disagree: only ${p.k} of ${p.n} reach ${f1(p.min)} m/s`,
      wind_high: (p) => `Too much wind: ${f1(p.w)} m/s aloft`,
      wind_strong: (p) => `Strong wind: ${f1(p.w)} m/s aloft`,
      snow: () => "Snow or ice",
      rain: (p) => `Rain: ${fmtNum(p.r, 2)} mm/h`,
      rain_prob: (p) => `${pct(p.p)} chance of rain`,
      rain_other: () => "Some forecasts show rain",
      storm: (p) => "Thunderstorm forecast" + (p.cape ? ` (CAPE ${p.cape} J/kg)` : ""),
      storm_prob: (p) => `${pct(p.p)} chance of a thunderstorm`,
      storm_other: () => "Some forecasts show thunderstorms",
      unstable: (p) => `Unstable air (CAPE ${p.cape} J/kg)`,
      fog: (p) => `Thick fog: visibility ${p.vis} m`,
    },
  },
  es: {
    locale: "es-ES",
    status: { go: ["APTO", "Apto"], caution: ["MARGINAL", "Marginal"], nogo: ["NO APTO", "No apto"], night: ["NOCHE", "Noche"], nodata: ["SIN DATOS", "Sin datos"] },
    compass: ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"],
    html: {
      next3: "Próximos 3 días",
      windTitle: "Viento entre 10 y 150 m", windHint: "línea: mejor modelo · banda: rango probable (10–90 % de las ejecuciones del conjunto)",
      probTitle: "Probabilidad de viento suficiente", probHint: "proporción de ejecuciones del conjunto que llegan al mínimo",
      heightsTitle: "Viento a cada altura", heightsHint: "mejor modelo · gris: por debajo del mínimo",
      rainTitle: "Lluvia", bestModel: "mejor modelo", rainProbTitle: "Probabilidad de lluvia", stormProbTitle: "Probabilidad de tormenta",
      sources: "De dónde salen los datos",
    },
    updated: (hm, age) => `Actualizado ${hm} (${age})`, refreshes: "se actualiza cada 5 min",
    justNow: "ahora mismo", minAgo: (n) => `hace ${n} min`, hAgo: (n) => `hace ${n} h`,
    stale: (age) => `<b>Este pronóstico se calculó ${age}.</b> El ordenador que lo calcula puede estar apagado; revisa las horas antes de decidir.`,
    testing: "<b>Se usa la clave de prueba de Windy para decidir.</b> Windy desordena a propósito los datos del plan de prueba.",
    demo: "<b>Datos de demostración.</b> Estos pronósticos son inventados para probar la página sin ninguna API.",
    cannotReach: "No se puede cargar el pronóstico: ",
    site: (s) => s.lat == null ? s.name : `${s.name} · ${s.lat.toFixed(4)}, ${s.lon.toFixed(4)} · suelo a ${s.elevation_m} m`,
    today: "hoy", tomorrow: "mañana", Today: "Hoy", Tomorrow: "Mañana",
    goHours: (n) => `<b>${n} h aptas</b>`, margHours: (n) => `${n} h marginales`, noGoHours: "Ninguna hora apta",
    now: "Ahora", noData: "Ningún pronóstico cubre esta hora todavía", sunDown: "Es de noche",
    allMet: (w) => `Se cumplen todos los límites · ${w} m/s entre 10 y 150 m`,
    noWindow: (d) => `<b>Ninguna hora apta</b> en los próximos ${d} días.`,
    goNow: (b, n) => `<b>APTO ahora</b> hasta las <b>${b}</b> (${n} h).`,
    nextWindow: (day, a, b, n) => `Próxima ventana apta: <b>${day} ${a}</b> – <b>${b}</b> (${n} h).`,
    more: (n) => `${n} ventana${n > 1 ? "s" : ""} más después.`,
    tile: {
      wind: "Viento 10–150 m", likely: (a, b, m) => `probable ${a}–${b} · mínimo ${m}`,
      chance: "Probabilidad de viento suficiente", runs: (n, m) => `de ${n} ejecuciones del conjunto llegan a ${m} m/s`, noEns: "sin datos de conjunto",
      rain: "Lluvia", none: "Ninguna", chanceOf: (p) => `${p} de probabilidad`, mustZero: "debe ser cero",
      storm: "Tormenta", forecast: "Prevista", possible: "Posible",
    },
    tip: {
      wind: "Viento 10–150 m", likely: "Rango probable (10–90 %)", spread: "Dispersión de pronósticos",
      chance: (m) => `Probabilidad de ≥ ${m} m/s`, at150: "A 150 m", from: "Procedente del", rain: "Lluvia", chance2: "de probabilidad",
      storm: "Tormenta", yes: "prevista", no: "no", vis: "Visibilidad", temp: "Temperatura", by: "Decide",
    },
    chart: {
      status: "Estado", dir: "dir", now: "Ahora", min: (m) => `mínimo ${m} m/s`, strong: (m) => `${m} m/s fuerte`,
      needed: (p) => `${p} necesario`, nogo: (v) => `${v} no apto`, notProvided: "este modelo no lo da", noEns: "sin datos de conjunto",
    },
    band: "Rango probable",
    src: {
      head: ["Modelo", "Malla", "Ejecuciones", "Actualizado", "Hasta", "Paso", "10–150 m a partir de", "Estado"],
      group: { "Open-Meteo": "Open-Meteo · decide", Windy: "Windy", "Open-Meteo ensemble": "Conjuntos · rango probable y probabilidades" },
      ok: "OK", waiting: "en espera", noKey: "sin clave", shownOnly: "solo se muestra: clave de prueba", never: "nunca",
      runs: (n) => `${n} ejecuciones`, points: (a, b) => `${a}/${b} puntos`,
    },
    hf: {
      none: () => "–", levels: () => "10 m + niveles de presión", law: () => "10 m + ley 1/7",
      measured: (a, b, e) => `medido ${a}–${b} m${e ? " + extrapolado" : ""}`, shear: (x) => `10 m + cizalladura de ${x}`,
    },
    fine: (L, d, windy) =>
      `Cada hora la decide el modelo más fino que la cubre (primero AROME HD, 1,3 km); los siguientes completan lo que ` +
      `le falta, como los códigos de tormenta. El viento es la media en altura entre ${d.heights[0]} y ` +
      `${d.heights[d.heights.length - 1]} m sobre el suelo. El rango probable y las probabilidades salen de cuatro ` +
      `conjuntos (ensembles), cada uno con el mismo peso. Límites: viento medio ≥ ${L.min_wind} m/s; sin lluvia ` +
      `(≥ ${L.rain_nogo} mm/h es no apto); sin tormenta; la niebla solo cuenta por debajo de ${L.visibility_nogo} m. ` +
      `No se usan las rachas. ` +
      (windy && !d.windy_used ? "Los datos de Windy se muestran para comparar y no deciden nada mientras su clave sea de prueba. " : "") +
      `Datos: Open-Meteo (CC BY 4.0)${windy ? " y Windy Point Forecast API" : ""}.`,
    reasons: {
      wind_low: (p) => `Falta viento: ${f1(p.w)} m/s, se necesitan ${f1(p.min)}`,
      wind_prob: (p) => `Solo un ${pct(p.p)} de probabilidad de ${f1(p.min)} m/s o más`,
      wind_disagree: (p) => `Los pronósticos no coinciden: solo ${p.k} de ${p.n} llegan a ${f1(p.min)} m/s`,
      wind_high: (p) => `Demasiado viento: ${f1(p.w)} m/s en altura`,
      wind_strong: (p) => `Viento fuerte: ${f1(p.w)} m/s en altura`,
      snow: () => "Nieve o hielo",
      rain: (p) => `Lluvia: ${fmtNum(p.r, 2)} mm/h`,
      rain_prob: (p) => `${pct(p.p)} de probabilidad de lluvia`,
      rain_other: () => "Algunos pronósticos dan lluvia",
      storm: (p) => "Tormenta prevista" + (p.cape ? ` (CAPE ${p.cape} J/kg)` : ""),
      storm_prob: (p) => `${pct(p.p)} de probabilidad de tormenta`,
      storm_other: () => "Algunos pronósticos dan tormenta",
      unstable: (p) => `Aire inestable (CAPE ${p.cape} J/kg)`,
      fog: (p) => `Niebla espesa: visibilidad ${p.vis} m`,
    },
  },
};

function initialLang() {
  try {
    const saved = localStorage.getItem("kitewind-lang");
    if (saved && I18N[saved]) return saved;
  } catch (e) { /* storage may be blocked */ }
  return (navigator.language || "en").toLowerCase().startsWith("es") ? "es" : "en";
}
let LANG = initialLang();
let T = I18N[LANG];
let fHM, fH, fDay, fDayLong, fKey;

function setLang(lang) {
  LANG = lang;
  T = I18N[lang];
  try { localStorage.setItem("kitewind-lang", lang); } catch (e) { /* not needed to work */ }
  const fmt = (opts) => new Intl.DateTimeFormat(T.locale, { timeZone: TZ, ...opts });
  fHM = fmt({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
  fH = new Intl.DateTimeFormat("en-GB", { timeZone: TZ, hour: "2-digit", hourCycle: "h23" });
  fDay = fmt({ weekday: "short", day: "numeric" });
  fDayLong = fmt({ weekday: "long", day: "numeric", month: "short" });
  fKey = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = T.html[el.dataset.i18n] || ""; });
  document.querySelectorAll(".lang button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.lang === lang)));
  if (DATA) render();
}

const localHour = (t) => +fH.format(t);
const ICONS = {
  go: '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>',
  caution: '<path d="M12 3L2 20h20L12 3z" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linejoin="round"/><path d="M12 10v4.5M12 17.2v.3" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>',
  nogo: '<path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>',
  night: '<path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5z" fill="currentColor"/>',
  nodata: '<path d="M6 12h12" stroke="currentColor" stroke-width="3" stroke-linecap="round"/>',
};
const icon = (s) => `<svg viewBox="0 0 24 24" aria-hidden="true">${ICONS[s] || ""}</svg>`;
const label = (s) => T.status[s][0];
const word = (s) => T.status[s][1];
const compass = (d) => T.compass[Math.round(d / 22.5) % 16];
const RAMP = ["#b7d3f6", "#9ec5f4", "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#1c5cab", "#184f95", "#104281"];
const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const isDark = () => css("--surface").toLowerCase() === "#1a1a19";
const probLevel = (p, L) => p == null ? "nodata" : p >= L.min_wind_probability ? "go" : p >= L.min_wind_probability / 2 ? "caution" : "nogo";
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const reasonText = (r) => (r.code && T.reasons[r.code] ? T.reasons[r.code](r.p || {}) : r.text);
const fmtNum = (x, nd) => x.toLocaleString(T.locale, { minimumFractionDigits: nd, maximumFractionDigits: nd });

let DATA = null;

async function load() {
  try {
    let url = window.KITEWIND_DATA || "api/forecast";
    if (/^https?:/.test(url)) url += (url.includes("?") ? "&" : "?") + "t=" + Math.floor(Date.now() / 60e3);
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const d = await r.json();
    if (!d) throw new Error("no data yet");
    // A published copy can be a few minutes old: start at the current hour.
    const now = Date.now();
    const current = d.hours.filter((h) => h.t + HOUR > now);
    if (current.length) d.hours = current;
    DATA = d;
    render();
  } catch (e) {
    document.getElementById("why").textContent = T.cannotReach + e.message;
  }
}

// ---------------------------------------------------------------- header, hero, days

function ageText(ms) {
  return ms < 90e3 ? T.justNow : ms < 90 * 60e3 ? T.minAgo(Math.round(ms / 60e3)) : T.hAgo(Math.round(ms / 3600e3));
}

function renderHeader(d) {
  document.getElementById("site").textContent = T.site(d.site);
  const age = Date.now() - d.generated;
  document.getElementById("meta").innerHTML = `${T.updated(fHM.format(d.generated), ageText(age))}<br>${T.refreshes}`;
  const b = document.getElementById("banner");
  if (age > STALE_MS) {
    b.hidden = false;
    b.className = "banner bad";
    b.innerHTML = T.stale(ageText(age));
    return;
  }
  const msg = { testing: d.windy_used ? T.testing : null, demo: T.demo }[d.key_mode];
  b.hidden = !msg;
  if (msg) { b.className = "banner"; b.innerHTML = msg; }
}

function windows(hours) {
  const out = [];
  let cur = null;
  hours.forEach((h, i) => {
    if (h.status === "go") {
      if (cur && cur.end === i) cur.end = i + 1;
      else { cur = { start: i, end: i + 1 }; out.push(cur); }
    }
  });
  return out;
}

function sameDay(a, b) { return fKey.format(a) === fKey.format(b); }
function dayName(t, now) {
  if (sameDay(t, now)) return T.today;
  if (sameDay(t, now + 24 * HOUR)) return T.tomorrow;
  return fDayLong.format(t);
}

function renderHero(d) {
  const hours = d.hours, h = hours[0];
  document.getElementById("hero").className = "hero " + h.status;
  const badge = document.getElementById("badge");
  badge.className = "badge " + h.status;
  badge.innerHTML = icon(h.status) + label(h.status);
  document.getElementById("when").textContent =
    `${T.now} · ${fDay.format(h.t)} ${fHM.format(h.t)}` + (h.primary ? ` · ${modelLabel(d, h.primary)}` : "");
  let why;
  if (h.status === "nodata") why = T.noData;
  else if (h.status === "night") why = T.sunDown + (h.reasons.length ? ` · ${reasonText(h.reasons[0])}` : "");
  else if (h.status === "go") why = T.allMet(fmtNum(h.wind, 1));
  else why = h.reasons.slice(0, 2).map(reasonText).join(" · ");
  document.getElementById("why").textContent = why;

  const w = windows(hours);
  const next = document.getElementById("next");
  if (!w.length) {
    next.innerHTML = T.noWindow(Math.round(hours.length / 24));
  } else {
    const a = hours[w[0].start].t, b = hours[w[0].end - 1].t + HOUR;
    const len = w[0].end - w[0].start;
    next.innerHTML = w[0].start === 0
      ? T.goNow(fHM.format(b), len)
      : T.nextWindow(dayName(a, d.generated), fHM.format(a), fHM.format(b), len);
    if (w.length > 1) next.innerHTML += ` <span class="hint">${T.more(w.length - 1)}</span>`;
  }

  const L = d.limits, c = h.checks || {}, tl = T.tile;
  const chance = (p) => (p == null ? "" : tl.chanceOf(pct(p)));
  const stormWord = h.storm ? tl.forecast : c.storm === "nogo" || c.storm === "caution" ? tl.possible : tl.none;
  const tiles = [
    ["wind", tl.wind, h.wind == null ? "–" : `${fmtNum(h.wind, 1)} <small>m/s</small>`,
      h.wind == null ? "" : tl.likely(fmtNum(h.wind_lo, 1), fmtNum(h.wind_hi, 1), L.min_wind)],
    ["wind", tl.chance, h.wind_prob == null ? "–" : pct(h.wind_prob),
      h.n_runs ? tl.runs(h.n_runs, L.min_wind) : tl.noEns],
    ["rain", tl.rain, h.rain == null ? "–" : h.rain < L.rain_nogo ? tl.none : `${fmtNum(h.rain, 2)} <small>mm/h</small>`,
      [chance(h.rain_prob), tl.mustZero].filter(Boolean).join(" · ")],
    ["storm", tl.storm, stormWord,
      [chance(h.storm_prob), h.cape != null ? `CAPE ${h.cape} J/kg` : ""].filter(Boolean).join(" · ")],
  ];
  document.getElementById("drivers").innerHTML = tiles.map(([k, name, value, sub], i) => {
    const lvl = i === 1 ? probLevel(h.wind_prob, L) : c[k] || "nodata";
    const tag = lvl === "nodata" ? "" : `<span class="lvl ${lvl}">${icon(lvl)}${word(lvl)}</span>`;
    return `<div class="driver"><div class="name">${name}${tag}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
  }).join("");
}

function statusColor(s) {
  return { go: css("--go"), caution: css("--caution"), nogo: css("--nogo"), night: css("--night"), nodata: css("--nodata") }[s];
}

function renderDays(d) {
  const groups = [];
  d.hours.forEach((h) => {
    const k = fKey.format(h.t);
    if (!groups.length || groups[groups.length - 1].k !== k) groups.push({ k, t: h.t, hours: [] });
    groups[groups.length - 1].hours.push(h);
  });
  document.getElementById("days").innerHTML = groups.map((g) => {
    const go = g.hours.filter((h) => h.status === "go").length;
    const mg = g.hours.filter((h) => h.status === "caution").length;
    const txt = go ? T.goHours(go) + (mg ? ` · ${T.margHours(mg)}` : "") : mg ? T.margHours(mg) : T.noGoHours;
    const bar = g.hours.map((h) => `<i style="background:${statusColor(h.status)}"></i>`).join("");
    const name = sameDay(g.t, d.generated) ? T.Today : sameDay(g.t, d.generated + 24 * HOUR) ? T.Tomorrow : fDay.format(g.t);
    return `<div class="day"><div class="d">${name}</div><div class="h">${txt}</div><div class="bar">${bar}</div></div>`;
  }).join("");
}

function modelLabel(d, id) {
  const m = d.models.find((x) => x.id === id);
  return m ? `${m.label} ${m.resolution}` : id;
}

// ---------------------------------------------------------------- SVG charts

const NS = "http://www.w3.org/2000/svg";
function S(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  if (parent) parent.appendChild(e);
  return e;
}

function frame(id, height, d) {
  const box = document.getElementById(id);
  box.innerHTML = "";
  const W = Math.max(300, box.clientWidth);
  const svg = S("svg", { viewBox: `0 0 ${W} ${height}`, height });
  box.appendChild(svg);
  const padL = 44, padR = 8, N = d.hours.length;
  const bw = (W - padL - padR) / N;
  const x = (i) => padL + i * bw;
  return { svg, W, H: height, padL, padR, N, bw, x, box };
}

function nightShade(f, d, top, bottom) {
  d.hours.forEach((h, i) => {
    if (!h.day) S("rect", { x: f.x(i), y: top, width: f.bw + 0.5, height: bottom - top, fill: css("--night-shade") }, f.svg);
  });
  d.hours.forEach((h, i) => {
    if (i > 0 && localHour(h.t) === 0)
      S("line", { x1: f.x(i), x2: f.x(i), y1: top, y2: bottom, stroke: css("--axis"), "stroke-width": 1 }, f.svg);
  });
}

function yAxis(f, y, ticks, top, bottom, unit) {
  ticks.forEach((v) => {
    const yy = y(v);
    if (yy < top - 1 || yy > bottom + 1) return;
    S("line", { x1: f.padL, x2: f.W - f.padR, y1: yy, y2: yy, stroke: css("--grid"), "stroke-width": 1 }, f.svg);
    const t = S("text", { x: f.padL - 6, y: yy + 3.5, "text-anchor": "end" }, f.svg);
    t.textContent = v + (unit && v === ticks[ticks.length - 1] ? " " + unit : "");
  });
}

function thresholdLine(f, yy, text, color, dash = "5 4") {
  S("line", { x1: f.padL, x2: f.W - f.padR, y1: yy, y2: yy, stroke: color, "stroke-width": 1.5, "stroke-dasharray": dash }, f.svg);
  if (text) {
    const t = S("text", { x: f.W - f.padR - 2, y: yy - 4, "text-anchor": "end", class: "lbl" }, f.svg);
    t.textContent = text;
  }
}

function hourTicks(f, d, y) {
  const every = f.bw * 6 > 26 ? 6 : 12;
  d.hours.forEach((h, i) => {
    const lh = localHour(h.t);
    if (lh % every === 0) {
      const t = S("text", { x: f.x(i), y, "text-anchor": "middle" }, f.svg);
      t.textContent = String(lh).padStart(2, "0");
    }
  });
}

// Paths with gaps where data is missing.
function linePath(d, xf, yf, key) {
  let p = "", pen = false;
  d.hours.forEach((h, i) => {
    const v = h[key];
    if (v == null) { pen = false; return; }
    p += (pen ? "L" : "M") + xf(i).toFixed(1) + "," + yf(v).toFixed(1);
    pen = true;
  });
  return p;
}
function bandPath(d, xf, yf, lo, hi) {
  const segs = [];
  let cur = [];
  d.hours.forEach((h, i) => {
    if (h[lo] == null || h[hi] == null) { if (cur.length) segs.push(cur); cur = []; return; }
    cur.push(i);
  });
  if (cur.length) segs.push(cur);
  return segs.map((s) => {
    const top = s.map((i) => `${xf(i).toFixed(1)},${yf(d.hours[i][hi]).toFixed(1)}`);
    const bot = s.slice().reverse().map((i) => `${xf(i).toFixed(1)},${yf(d.hours[i][lo]).toFixed(1)}`);
    return "M" + top.join("L") + "L" + bot.join("L") + "Z";
  }).join("");
}

const charts = [];  // {f, xh} for the crosshair

function hoverLayer(f, top, bottom) {
  const xh = S("line", { y1: top, y2: bottom, stroke: css("--ink-2"), "stroke-width": 1, opacity: 0, "pointer-events": "none" }, f.svg);
  const hit = S("rect", { x: f.padL, y: 0, width: f.W - f.padL - f.padR, height: f.H, fill: "transparent" }, f.svg);
  charts.push({ f, xh });
  const move = (ev) => {
    const pt = ev.touches ? ev.touches[0] : ev;
    const r = f.svg.getBoundingClientRect();
    const sx = (pt.clientX - r.left) * (f.W / r.width);
    const i = Math.max(0, Math.min(f.N - 1, Math.floor((sx - f.padL) / f.bw)));
    showHover(i, pt.clientX, pt.clientY);
  };
  hit.addEventListener("mousemove", move);
  hit.addEventListener("touchstart", move, { passive: true });
  hit.addEventListener("touchmove", move, { passive: true });
  hit.addEventListener("mouseleave", hideHover);
}

function showHover(i, cx, cy) {
  charts.forEach(({ f, xh }) => {
    const xm = f.x(i) + f.bw / 2;
    xh.setAttribute("x1", xm); xh.setAttribute("x2", xm); xh.setAttribute("opacity", 0.7);
  });
  const h = DATA.hours[i], L = DATA.limits, tp = T.tip;
  const tip = document.getElementById("tooltip");
  const row = (k, v) => `<div class="row"><span>${k}</span><b>${v}</b></div>`;
  let html = `<div class="t"><span class="lvl ${h.status}">${icon(h.status)}${label(h.status)}</span>${fDay.format(h.t)} ${fHM.format(h.t)}</div>`;
  if (h.reasons && h.reasons.length) html += "<ul>" + h.reasons.map((r) => `<li>${esc(reasonText(r))}</li>`).join("") + "</ul>";
  if (h.status !== "nodata") {
    const chance = (p) => (p != null ? ` · ${pct(p)} ${tp.chance2}` : "");
    html += row(tp.wind, `${fmtNum(h.wind, 1)} m/s`);
    html += row(h.band === "ensemble" ? tp.likely : tp.spread, `${fmtNum(h.wind_lo, 1)}–${fmtNum(h.wind_hi, 1)} m/s`);
    if (h.wind_prob != null) html += row(tp.chance(L.min_wind), pct(h.wind_prob));
    html += row(tp.at150, `${fmtNum(h.profile[h.profile.length - 1], 1)} m/s`);
    if (h.dir != null) html += row(tp.from, `${compass(h.dir)} (${h.dir}°)`);
    html += row(tp.rain, h.rain == null ? "–" : `${fmtNum(h.rain, 2)} mm/h` + chance(h.rain_prob));
    html += row(tp.storm, (h.storm ? tp.yes : tp.no) + chance(h.storm_prob));
    if (h.vis != null && h.vis < 5000) html += row(tp.vis, `${Math.round(h.vis)} m`);
    if (h.temp != null) html += row(tp.temp, `${fmtNum(h.temp, 1)} °C`);
    html += row(tp.by, modelLabel(DATA, h.primary));
    if (h.models && h.models.length > 1)
      html += `<div class="models">` + h.models.map(([n, v]) => `<span>${esc(n)} <b>${v == null ? "–" : fmtNum(v, 1)}</b></span>`).join("") + "</div>";
  }
  tip.innerHTML = html;
  tip.hidden = false;
  const host = document.getElementById("charts").getBoundingClientRect();
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  let left = cx - host.left + 16;
  if (left + tw > host.width - 8) left = cx - host.left - tw - 16;
  let top = cy - host.top - th / 2;
  top = Math.max(8, Math.min(host.height - th - 8, top));
  tip.style.left = Math.max(8, left) + "px";
  tip.style.top = top + "px";
}
function hideHover() {
  charts.forEach(({ xh }) => xh.setAttribute("opacity", 0));
  document.getElementById("tooltip").hidden = true;
}

function drawStatus(d) {
  const f = frame("c-status", 58, d);
  d.hours.forEach((h, i) => {
    S("rect", { x: f.x(i) + 0.5, y: 18, width: Math.max(1, f.bw - 1), height: 22, rx: 2, fill: statusColor(h.status) }, f.svg);
    if (i === 0 || localHour(h.t) === 0) {
      const t = S("text", { x: f.x(i) + 2, y: 12, class: "lbl" }, f.svg);
      t.textContent = i === 0 ? T.chart.now : fDay.format(h.t);
    }
  });
  const lab = S("text", { x: f.padL - 6, y: 33, "text-anchor": "end" }, f.svg);
  lab.textContent = T.chart.status;
  hourTicks(f, d, 54);
  hoverLayer(f, 16, 42);
}

function drawWind(d) {
  const f = frame("c-wind", 230, d), L = d.limits;
  const top = 22, bottom = 206;
  const hi = Math.max(10, ...d.hours.map((h) => h.wind_hi || 0), ...d.hours.map((h) => h.wind_top || 0));
  const ymax = Math.ceil((hi + 1) / 2) * 2;
  const y = (v) => bottom - (v / ymax) * (bottom - top);
  const xm = (i) => f.x(i) + f.bw / 2;
  nightShade(f, d, top, bottom);
  S("rect", { x: f.padL, y: y(L.min_wind), width: f.W - f.padL - f.padR, height: bottom - y(L.min_wind), fill: css("--low-zone") }, f.svg);
  const step = ymax > 16 ? 4 : 2;
  yAxis(f, y, Array.from({ length: ymax / step + 1 }, (_, k) => k * step), top, bottom, "m/s");
  S("path", { d: bandPath(d, xm, y, "wind_lo", "wind_hi"), fill: css("--band"), stroke: "none" }, f.svg);
  thresholdLine(f, y(L.min_wind), T.chart.min(L.min_wind), css("--nogo"));
  if (ymax >= L.max_wind_caution) thresholdLine(f, y(L.max_wind_caution), T.chart.strong(L.max_wind_caution), css("--caution"));
  S("path", { d: linePath(d, xm, y, "wind"), fill: "none", stroke: css("--line"), "stroke-width": 2.25, "stroke-linejoin": "round" }, f.svg);
  // Direction arrows (pointing where the wind goes), every 3 hours, or 6 on narrow screens.
  const arrowEvery = f.bw * 3 >= 16 ? 3 : 6;
  d.hours.forEach((h, i) => {
    if (h.dir == null || localHour(h.t) % arrowEvery !== 0) return;
    const g = S("g", { transform: `translate(${xm(i)},10) rotate(${h.dir + 180})` }, f.svg);
    S("path", { d: "M0,-6 L0,6 M-3.5,2.5 L0,6.5 L3.5,2.5", fill: "none", stroke: css("--ink-2"), "stroke-width": 1.4, "stroke-linecap": "round" }, g);
  });
  const t = S("text", { x: f.padL - 6, y: 13, "text-anchor": "end" }, f.svg);
  t.textContent = T.chart.dir;
  hourTicks(f, d, 222);
  hoverLayer(f, top, bottom);
}

function drawProb(d) {
  const f = frame("c-prob", 74, d), L = d.limits;
  const top = 6, bottom = 54;
  const y = (p) => bottom - p * (bottom - top);
  nightShade(f, d, top, bottom);
  yAxis(f, (v) => y(v / 100), [0, 50, 100], top, bottom, "%");
  d.hours.forEach((h, i) => {
    if (h.wind_prob == null || h.wind_prob < 0.005) return;
    S("rect", { x: f.x(i) + 0.5, y: y(h.wind_prob), width: Math.max(1, f.bw - 1), height: bottom - y(h.wind_prob), rx: 1, fill: css("--line") }, f.svg);
  });
  thresholdLine(f, y(L.min_wind_probability), T.chart.needed(pct(L.min_wind_probability)), css("--caution"));
  hourTicks(f, d, 70);
  hoverLayer(f, top, bottom);
}

function drawHeights(d) {
  const rows = d.heights.slice().reverse();
  const rh = 15, top = 2;
  const f = frame("c-heights", top + rows.length * rh + 22, d), L = d.limits;
  const ramp = isDark() ? RAMP.slice().reverse() : RAMP;
  const color = (v) => v < L.min_wind ? css("--nodata")
    : ramp[Math.min(ramp.length - 1, Math.floor(((v - L.min_wind) / 10) * ramp.length))];
  rows.forEach((hgt, r) => {
    const k = d.heights.length - 1 - r;
    d.hours.forEach((h, i) => {
      if (!h.profile) return;
      S("rect", { x: f.x(i), y: top + r * rh, width: f.bw + 0.4, height: rh - 1, fill: color(h.profile[k]) }, f.svg);
    });
    if (r % 2 === 0 || r === rows.length - 1) {
      const t = S("text", { x: f.padL - 6, y: top + r * rh + rh / 2 + 3.5, "text-anchor": "end" }, f.svg);
      t.textContent = `${hgt} m`;
    }
  });
  nightShade(f, d, top, top + rows.length * rh);
  hourTicks(f, d, top + rows.length * rh + 16);
  hoverLayer(f, top, top + rows.length * rh);
}

function smallChart(id, d, key, lines, unit, opts = {}) {
  const f = frame(id, 128, d);
  const top = 8, bottom = 104;
  const sc = opts.scale || 1;
  const vals = d.hours.map((h) => h[key]).filter((v) => v != null).map((v) => v * sc);
  const ymax = opts.ymax(vals);
  const y = (v) => bottom - (Math.min(v, ymax) / ymax) * (bottom - top);
  nightShade(f, d, top, bottom);
  yAxis(f, y, opts.ticks(ymax), top, bottom, unit);
  if (!vals.length) {
    const t = S("text", { x: f.W / 2, y: (top + bottom) / 2, "text-anchor": "middle", class: "lbl" }, f.svg);
    t.textContent = opts.empty || T.chart.notProvided;
  } else {
    d.hours.forEach((h, i) => {
      const v = h[key] == null ? null : h[key] * sc;
      if (v == null || v < (opts.min ?? 0.01)) return;
      S("rect", { x: f.x(i) + 0.5, y: y(v), width: Math.max(1, f.bw - 1), height: bottom - y(v), rx: 1, fill: css("--line") }, f.svg);
    });
  }
  lines.forEach(([v, text, color]) => { if (v <= ymax) thresholdLine(f, y(v), text, css(color)); });
  hourTicks(f, d, 122);
  hoverLayer(f, top, bottom);
}

function drawLegend() {
  const item = (c, t) => `<span><i style="background:${c}"></i>${t}</span>`;
  document.getElementById("legend").innerHTML = [
    item(css("--go"), word("go")), item(css("--caution"), word("caution")), item(css("--nogo"), word("nogo")),
    item(css("--night"), word("night")), item(css("--band"), T.band),
  ].join("");
}

function renderSources(d) {
  const s = T.src;
  const ago = (t) => (t ? ageText(d.generated - t) : s.never);
  const order = { "Open-Meteo": 0, Windy: 1, "Open-Meteo ensemble": 2 };
  const hasWindy = d.models.some((m) => m.source === "Windy");
  const rows = d.models.slice().sort((a, b) => order[a.source] - order[b.source]).map((m, k, all) => {
    let state = m.error && (!m.fetched || m.error.t > m.fetched)
      ? `<span class="err">${esc(m.error.text)}</span>`
      : m.fetched ? s.ok : d.key_mode === "none" && m.source === "Windy" ? s.noKey : s.waiting;
    if (m.source === "Windy" && !m.used && m.fetched) state = s.shownOnly;
    const head = k === 0 || all[k - 1].source !== m.source
      ? `<tr class="group"><td colspan="8">${s.group[m.source]}</td></tr>` : "";
    const runs = m.points ? s.points(m.points[0], m.points[1]) : m.source === "Open-Meteo ensemble" ? s.runs(m.runs) : "1";
    const hf = m.hf && T.hf[m.hf[0]] ? T.hf[m.hf[0]](...m.hf.slice(1)) : esc(m.heights_from || "–");
    return head + `<tr><td><b>${esc(m.label)}</b></td><td>${m.resolution}</td><td>${runs}</td>` +
      `<td>${ago(m.fetched)}</td><td>${m.until ? fDay.format(m.until) + " " + fHM.format(m.until) : "–"}</td>` +
      `<td>${m.step_h ? m.step_h + " h" : "–"}</td><td>${esc(hf)}</td><td>${state}</td></tr>`;
  }).join("");
  document.getElementById("models").innerHTML = "<tr>" + s.head.map((x) => `<th>${x}</th>`).join("") + "</tr>" + rows;
  document.getElementById("fine").innerHTML = T.fine(d.limits, d, hasWindy) +
    (d.budget ? ` Windy: ${d.budget.used_today}/${d.budget.cap}.` : "");
}

function render() {
  const d = DATA;
  charts.length = 0;
  renderHeader(d);
  renderHero(d);
  renderDays(d);
  drawLegend();
  drawStatus(d);
  drawWind(d);
  drawProb(d);
  drawHeights(d);
  const L = d.limits;
  const pctTicks = { ymax: () => 100, ticks: () => [0, 50, 100], scale: 100, min: 0.5, empty: T.chart.noEns };
  smallChart("c-rain", d, "rain", [[L.rain_nogo, T.chart.nogo(L.rain_nogo), "--nogo"]], "mm/h",
    { min: 0.005, ymax: (v) => Math.max(0.5, ...v), ticks: (m) => [0, +(m / 2).toFixed(2), +m.toFixed(2)] });
  smallChart("c-rainp", d, "rain_prob", [[L.rain_probability_caution * 100, "", "--caution"]], "%", pctTicks);
  smallChart("c-storm", d, "storm_prob", [[L.storm_probability_nogo * 100, T.chart.nogo(pct(L.storm_probability_nogo)), "--nogo"]], "%", pctTicks);
  renderSources(d);
}

document.querySelectorAll(".lang button").forEach((b) => b.addEventListener("click", () => setLang(b.dataset.lang)));
let resizeTimer;
window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => DATA && render(), 150); });
window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => DATA && render());
// Reopening the home-screen app should show fresh data at once.
document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") load(); });
setLang(LANG);
load();
setInterval(load, 5 * 60e3);
