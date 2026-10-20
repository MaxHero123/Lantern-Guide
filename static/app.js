/* Lantern Guide front end. Plain JavaScript, no build step. */
"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const main = $("#main");
let S = { profile: null, done: {}, added_events: [], settings: { text_size: "L" } };
let screen = "home";
let lastCheck = null;          // {text, sender, result}
let readText = "";

// ------------------------------------------------------------------ helpers
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pad = (n) => String(n).padStart(2, "0");
const keyOf = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const todayKey = () => keyOf();
const dateOf = (k) => { const [y, m, d] = k.split("-").map(Number); return new Date(y, m - 1, d); };
const addDays = (k, n) => { const d = dateOf(k); d.setDate(d.getDate() + n); return keyOf(d); };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : "");
function fmtTime(hhmm) {
  if (!hhmm) return "";
  let [h, m] = hhmm.split(":").map(Number);
  const ap = h >= 12 ? "PM" : "AM"; h = h % 12 || 12;
  return `${h}:${pad(m)} ${ap}`;
}
function fmtDate(iso, opts = {}) {
  const dt = dateOf(iso), diff = Math.round((dt - dateOf(todayKey())) / 86400000);
  const label = dt.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric", ...(dt.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}) });
  if (opts.plain) return label;
  return diff === 0 ? `Today, ${label}` : diff === 1 ? `Tomorrow, ${label}` : label;
}
const shortDate = (iso) => dateOf(iso).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
function minutesOf(hhmm) { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; }
const toHHMM = (mins) => `${pad(Math.floor(mins / 60) % 24)}:${pad(mins % 60)}`;
function parseLooseTime(s) {                       // "6:00 PM" -> "18:00"; "14:30" -> "14:30"; "All day" -> ""
  const m = String(s || "").match(/(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?/i);
  if (!m) return "";
  let h = +m[1], mm = +(m[2] || 0); const ap = (m[3] || "").toLowerCase();
  if (ap.startsWith("p") && h < 12) h += 12; if (ap.startsWith("a") && h === 12) h = 0;
  return h < 24 && mm < 60 ? `${pad(h)}:${pad(mm)}` : "";
}
const nowMins = () => { const d = new Date(); return d.getHours() * 60 + d.getMinutes(); };
function untilText(diff) {                          // minutes from now -> "In 25 minutes", "Now", "40 minutes ago"
  const a = Math.abs(diff), h = Math.floor(a / 60), m = a % 60;
  const span = h ? `${h} hour${h > 1 ? "s" : ""}${m ? " " + m + " min" : ""}` : `${m} minute${m === 1 ? "" : "s"}`;
  return diff === 0 ? "Now" : diff > 0 ? `In ${span}` : `${span} ago`;
}
const durText = (m) => (m >= 60 ? `${m / 60} hour${m > 60 ? "s" : ""}` : `${m} minutes`);
async function api(path, body) {
  const r = await fetch(path, body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({ error: "The app didn't answer. Is it still running?" }));
  if (!r.ok) throw Object.assign(new Error(j.error || "Something went wrong"), { detail: j.detail });
  return j;
}
function toast(msg, ms = 3500) { const t = $("#toast"); t.textContent = msg; t.hidden = false; clearTimeout(toast._t); toast._t = setTimeout(() => (t.hidden = true), ms); }
function speak(text) {
  if (!("speechSynthesis" in window)) return toast("Reading aloud isn't available in this browser.");
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text); u.rate = 0.9; speechSynthesis.speak(u);
}
const store = {                                     // small per-browser memory; the app works without it
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
const svg = (d, cls = "") => `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true">${d}</svg>`;
// [value, name, main color, background color] for the Colors choice in Settings
const THEMES = [["lantern", "Lantern", "#14213D", "#F6F2EA"], ["white", "Clear white", "#0A2A5E", "#FFFFFF"],
  ["garden", "Garden", "#1D4433", "#EEF3EA"], ["plum", "Plum and gold", "#3B1E4D", "#F6F1F4"]];
const ICON = {
  refresh: svg('<path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 4v7h-7"/>'),
  shield: svg('<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m8.5 12 2.5 2.5 4.5-5"/>'),
  read: svg('<circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5M8 10.5h5M10.5 8v5"/>'),
  day: svg('<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4M8 14h3M13 14h3M8 17h3"/>'),
  star: svg('<path d="m12 3.5 2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>'),
  hands: svg('<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/><path d="M9.5 11.5h5M12 9v5"/>'),
  news: svg('<rect x="3.5" y="4.5" width="14" height="15" rx="1.5"/><path d="M17.5 8.5h3v9a2 2 0 0 1-2 2H5.5M7 8.5h7M7 12h7M7 15.5h4"/>'),
  places: svg('<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>'),
  sun: svg('<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M2.5 12H5M19 12h2.5M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8"/>'),
  lock: svg('<rect x="5" y="10.5" width="14" height="10" rx="2"/><path d="M8 10.5V7.5a4 4 0 0 1 8 0v3"/>'),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  check: svg('<path d="m5 12.5 4.5 4.5L19 7.5"/>'),
  phone: svg('<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z"/>'),
  heads: svg('<path d="M12 8v5M12 16.5v.5"/><path d="M10.3 3.9 2.6 17.3A2 2 0 0 0 4.3 20h15.4a2 2 0 0 0 1.7-2.7L13.7 3.9a2 2 0 0 0-3.4 0z"/>'),
  scam: '<svg class="icon" viewBox="0 0 48 48" aria-hidden="true"><path d="M24 5 3 42h42z" fill="#B3261E"/><path d="M24 18v11" stroke="#fff" stroke-width="4" stroke-linecap="round"/><circle cx="24" cy="35" r="2.6" fill="#fff"/></svg>',
  junk: '<svg class="icon" viewBox="0 0 48 48" aria-hidden="true"><rect x="6" y="10" width="36" height="28" rx="4" fill="#6B4E16"/><path d="m8 13 16 12 16-12" stroke="#fff" stroke-width="3" fill="none"/><path d="M14 44h20" stroke="#6B4E16" stroke-width="3"/></svg>',
  unsure: '<svg class="icon" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="20" fill="#9A6200"/><path d="M18.5 18.5a5.5 5.5 0 1 1 8 4.9c-1.6.8-2.5 1.9-2.5 3.6v1" stroke="#fff" stroke-width="3.5" fill="none" stroke-linecap="round"/><circle cx="24" cy="34" r="2.4" fill="#fff"/></svg>',
  notice: '<svg class="icon" viewBox="0 0 48 48" aria-hidden="true"><path d="M24 4 7 10v11c0 11 7.5 18.5 17 21 9.5-2.5 17-10 17-21V10z" fill="#2E4F86"/><path d="m16 24 6 6 11-12" stroke="#fff" stroke-width="4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  normal: '<svg class="icon" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="20" fill="#2E7D4F"/><path d="m14 24.5 7 7 13-14" stroke="#fff" stroke-width="4.5" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>',
};

// ------------------------------------------------------------------ time picker: big up/down buttons, no scrolling lists
const ARROW_UP = svg('<path d="m6 15 6-6 6 6"/>'), ARROW_DOWN = svg('<path d="m6 9 6 6 6-6"/>');
function timePicker(id, value, { label = "Time", optional = false, toggleText = "", fallback = "08:00" } = {}) {
  const on = !!value, [h, m] = (value || fallback).split(":").map(Number);
  return `<div class="tp${on ? "" : " off"}" id="${id}" data-h="${h}" data-m="${m - (m % 5)}" data-on="${on}" role="group" aria-label="${esc(label)}">
    ${optional ? `<button type="button" class="tp-toggle" data-tp="toggle" aria-pressed="${on}"><span class="box"></span>${esc(toggleText || label)}</button>` : ""}
    <div class="tp-body">
      <div class="tp-col"><span class="tp-cap">Hour</span>
        <button type="button" class="tp-btn" data-tp="h+" aria-label="Later hour">${ARROW_UP}</button>
        <output class="tp-val" data-part="h"></output>
        <button type="button" class="tp-btn" data-tp="h-" aria-label="Earlier hour">${ARROW_DOWN}</button></div>
      <div class="tp-colon">:</div>
      <div class="tp-col"><span class="tp-cap">Minutes</span>
        <button type="button" class="tp-btn" data-tp="m+" aria-label="5 minutes later">${ARROW_UP}</button>
        <output class="tp-val" data-part="m"></output>
        <button type="button" class="tp-btn" data-tp="m-" aria-label="5 minutes earlier">${ARROW_DOWN}</button></div>
      <div class="tp-ampm"><button type="button" data-tp="am">AM</button><button type="button" data-tp="pm">PM</button></div>
    </div></div>`;
}
function drawPickers(root = document) {
  $$(".tp", root).forEach((el) => {
    const h = +el.dataset.h, m = +el.dataset.m, on = el.dataset.on === "true";
    el.querySelector('[data-part="h"]').textContent = h % 12 || 12;
    el.querySelector('[data-part="m"]').textContent = pad(m);
    el.querySelector('[data-tp="am"]').setAttribute("aria-pressed", h < 12);
    el.querySelector('[data-tp="pm"]').setAttribute("aria-pressed", h >= 12);
    el.classList.toggle("off", !on);
    const t = el.querySelector('[data-tp="toggle"]'); if (t) t.setAttribute("aria-pressed", on);
    $$(".tp-body button", el).forEach((b) => (b.disabled = !on));
  });
}
function getTime(id) { const el = document.getElementById(id); if (!el || el.dataset.on !== "true") return ""; return `${pad(el.dataset.h)}:${pad(el.dataset.m)}`; }
function setTime(id, hhmm) { const el = document.getElementById(id); if (!el) return; const [h, m] = hhmm.split(":").map(Number); Object.assign(el.dataset, { h, m, on: "true" }); drawPickers(el.parentElement); }
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-tp]"); if (!b) return;
  const el = b.closest(".tp"); let h = +el.dataset.h, m = +el.dataset.m;
  const pm = h >= 12, h12 = h % 12;
  switch (b.dataset.tp) {
    case "h+": h = ((h12 + 1) % 12) + (pm ? 12 : 0); break;          // the hour cycles 12, 1, 2 ... 11; AM/PM stays as chosen
    case "h-": h = ((h12 + 11) % 12) + (pm ? 12 : 0); break;
    case "m+": m = (m + 5) % 60; break;
    case "m-": m = (m + 55) % 60; break;
    case "am": h = h12; break;
    case "pm": h = h12 + 12; break;
    case "toggle": el.dataset.on = el.dataset.on !== "true"; break;
  }
  el.dataset.h = h; el.dataset.m = m; drawPickers(el.parentElement);
  el.dispatchEvent(new CustomEvent("timechange", { bubbles: true }));
});

// ------------------------------------------------------------------ month calendar (for the planner and for picking a date)
const calOpts = {};
function calendarHTML(id, opts) {
  calOpts[id] = opts;                      // {sel, view, marks: Set of keys, noPast, onPick}
  const { sel, view, marks = new Set(), noPast } = opts;
  const [y, mo] = view.split("-").map(Number), first = new Date(y, mo - 1, 1), today = todayKey();
  const startPad = first.getDay(), daysIn = new Date(y, mo, 0).getDate();
  const cells = [];
  for (let i = 0; i < startPad; i++) cells.push(`<span></span>`);
  for (let d = 1; d <= daysIn; d++) {
    const k = `${y}-${pad(mo)}-${pad(d)}`, past = k < today;
    cells.push(`<button type="button" class="cal-day${k === today ? " today" : ""}${past ? " past" : ""}" data-cal-day="${k}" aria-pressed="${k === sel}"
      ${noPast && past ? "disabled" : ""} aria-label="${esc(fmtDate(k, { plain: true }))}${marks.has(k) ? ", has plans" : ""}">${d}${marks.has(k) ? '<i class="dot"></i>' : ""}</button>`);
  }
  const monthName = first.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const prevDisabled = noPast && view <= today.slice(0, 7);
  return `<div class="cal" id="${id}">
    <div class="cal-head"><button type="button" class="cal-nav" data-cal="prev" aria-label="Previous month" ${prevDisabled ? "disabled" : ""}>${svg('<path d="m15 6-6 6 6 6"/>')}</button>
      <strong>${monthName}</strong><button type="button" class="cal-nav" data-cal="next" aria-label="Next month">${svg('<path d="m9 6 6 6-6 6"/>')}</button></div>
    <div class="cal-grid">${DAYS.map((d) => `<span class="cal-wd">${d.slice(0, 2)}</span>`).join("")}${cells.join("")}</div></div>`;
}
function redrawCal(id) { const el = document.getElementById(id); if (el) el.outerHTML = calendarHTML(id, calOpts[id]); }
document.addEventListener("click", (e) => {
  const b = e.target.closest("[data-cal], [data-cal-day]"); if (!b) return;
  const id = b.closest(".cal").id, o = calOpts[id]; if (!o) return;
  if (b.dataset.cal) {
    const [y, m] = o.view.split("-").map(Number), d = new Date(y, m - 1 + (b.dataset.cal === "next" ? 1 : -1), 1);
    o.view = `${d.getFullYear()}-${pad(d.getMonth() + 1)}`; redrawCal(id); return;
  }
  o.sel = b.dataset.calDay; redrawCal(id); o.onPick?.(o.sel);
});

// ------------------------------------------------------------------ the schedule: daily routine + planner entries
const PLAN_KINDS = { appointment: "Appointment", event: "Event", volunteer: "Volunteering", special: "Special day", visit: "Visit", game: "Game", plan: "Plan" };
const KIND_LABEL = { medicine: "Medicine", meal: "Meal", activity: "Activity", ...PLAN_KINDS };
function itemsFor(k = todayKey()) {
  const p = S.profile; if (!p) return [];
  const items = [], dow = dateOf(k).getDay();
  (p.medications || []).forEach((m, i) => (m.times || []).forEach((t) =>
    items.push({ id: `med-${i}-${t}`, time: t, kind: "medicine", title: `Take ${m.name}`, duration: 0,
      detail: [m.dose, m.with_food ? "with food" : ""].filter(Boolean).join(", ") })));
  for (const [mk, label] of [["breakfast", "Breakfast"], ["lunch", "Lunch"], ["dinner", "Dinner"]])
    if (p.meals?.[mk]) items.push({ id: `meal-${mk}`, time: p.meals[mk], kind: "meal", title: label, detail: "", duration: 45, remind: !!p.meal_remind?.[mk] });
  (p.activities || []).forEach((a, i) => { if (a.days?.includes(dow) && a.time) items.push({ id: `act-${i}`, time: a.time, kind: "activity", title: a.name, detail: a.place || "", duration: a.duration || 60 }); });
  (S.added_events || []).forEach((e) => {
    if (e.date !== k) return;
    const t = parseLooseTime(e.time);
    items.push({ id: `ev-${e.id}`, entry: e, time: t, allDay: !t, kind: e.kind || "appointment", title: e.title,
      detail: [e.place, e.note].filter(Boolean).join(" · "), duration: e.duration || 60 });
  });
  return items.sort((a, b) => (a.allDay ? "" : a.time).localeCompare(b.allDay ? "" : b.time));
}
const isDone = (id, k = todayKey()) => (S.done[k] || []).includes(id);
const remindable = (it) => !it.allDay && (it.kind !== "meal" || it.remind);   // meals only if they asked for meal reminders
function nextItem() {
  const mins = nowMins(), open = itemsFor().filter((it) => remindable(it) && !isDone(it.id));
  const overdue = open.filter((it) => minutesOf(it.time) <= mins && mins - minutesOf(it.time) <= 120 && it.kind === "medicine");
  return overdue[0] || open.find((it) => minutesOf(it.time) > mins) || null;
}
/* The next few hours: things due now (medicine stays for 2 hours until marked done) and coming up. Meals are left out. */
function upcomingWindow(hours = 6) {
  const mins = nowMins();
  return itemsFor().filter((it) => remindable(it) && !isDone(it.id)).filter((it) => {
    const diff = minutesOf(it.time) - mins;
    return (diff > 0 && diff <= hours * 60) || (diff <= 0 && diff > (it.kind === "medicine" ? -120 : -30));
  }).map((it) => ({ ...it, diff: minutesOf(it.time) - mins }));
}
/* Gentle heads-ups when two plans overlap, are back to back, or a medicine falls in the middle of an outing. */
function conflictsFor(items) {
  const blocks = items.filter((it) => !it.allDay && it.time && !["meal", "medicine"].includes(it.kind))
    .map((it) => ({ ...it, s: minutesOf(it.time), e: minutesOf(it.time) + (it.duration || 60) }));
  // (meals are handled separately below, so they never "clash" with an ordinary activity)
  const out = [];
  for (let i = 0; i < blocks.length; i++) for (let j = i + 1; j < blocks.length; j++) {
    const [a, b] = blocks[i].s <= blocks[j].s ? [blocks[i], blocks[j]] : [blocks[j], blocks[i]];
    if (b.s < a.e) out.push({ ids: [a.id, b.id], text: `${a.title} (${fmtTime(a.time)}) and ${b.title} (${fmtTime(b.time)}) overlap. You may want to move one of them.` });
    else if (b.s - a.e < 15) out.push({ ids: [a.id, b.id], text: `${a.title} ends right when ${b.title} starts at ${fmtTime(b.time)}. Leave a little time to get there.` });
  }
  items.filter((it) => it.kind === "medicine").forEach((m) => {
    const t = minutesOf(m.time), b = blocks.find((x) => t >= x.s && t < x.e && !x.entry?.at_home);   // at home, nothing to bring
    if (b) out.push({ ids: [m.id, b.id], text: `Your ${fmtTime(m.time)} medicine (${m.title.replace(/^Take /, "")}) falls during ${b.title}. Bring it with you, or ask your doctor or pharmacist whether you can take it a little earlier.` });
  });
  items.filter((it) => it.kind === "meal" && it.time).forEach((m) => {     // plans that land on a usual mealtime
    const s = minutesOf(m.time), e = s + 45, b = blocks.find((x) => x.entry && !x.entry.at_home && x.s < e && s < x.e);
    if (b) out.push({ ids: [m.id, b.id], text: `${b.title} (${fmtTime(b.time)}) is around your usual ${m.title.toLowerCase()} time. Eat before you go, plan to eat there, or bring a snack.` });
  });
  return out;
}
function conflictBox(list) {
  if (!list.length) return "";
  return `<div class="heads">${ICON.heads}<div><strong>Just so you know</strong>${list.map((c) => `<p>${esc(c.text)}</p>`).join("")}</div></div>`;
}

// ------------------------------------------------------------------ reminders: a big pop-up, a soft chime, an optional spoken
// reminder, and a computer notification (while Lantern Guide is open in any tab)
let audioCtx = null;
document.addEventListener("pointerdown", () => {                 // browsers only allow sound after the first tap
  try { audioCtx ??= new (window.AudioContext || window.webkitAudioContext)(); audioCtx.resume(); } catch {}
}, { passive: true });
function chime() {
  if (!audioCtx || store.get("lantern-chime", true) === false) return;
  const t0 = audioCtx.currentTime;
  [[659.3, 0], [880, 0.28]].forEach(([f, d]) => {
    const o = audioCtx.createOscillator(), g = audioCtx.createGain();
    o.type = "sine"; o.frequency.value = f; o.connect(g); g.connect(audioCtx.destination);
    g.gain.setValueAtTime(0.0001, t0 + d); g.gain.exponentialRampToValueAtTime(0.35, t0 + d + 0.03);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + d + 1.4); o.start(t0 + d); o.stop(t0 + d + 1.5);
  });
}
const remQueue = [];
function showReminder(r) {
  remQueue.push(r); if (remQueue.length === 1) drawReminder();
  chime();
  if (store.get("lantern-voice", true)) speak(`${r.when}. ${r.title}. ${r.detail || ""}`);
  if ("Notification" in window && Notification.permission === "granted") {
    try {
      const n = new Notification(`${r.when}: ${r.title}`, { body: r.detail || "From Lantern Guide", icon: "/lantern.svg", tag: r.tag, requireInteraction: r.kind === "now" });
      n.onclick = () => { window.focus(); n.close(); };
    } catch {}
  }
}
function drawReminder() {
  const r = remQueue[0], box = $("#remind");
  if (!r) { box.hidden = true; return; }
  $("#remWhen").textContent = r.when; $("#remTitle").textContent = r.title; $("#remDetail").textContent = r.detail || "";
  $("#remDetail").hidden = !r.detail;
  box.querySelector(".remcard").classList.toggle("now", r.kind === "now");
  $("#remBtns").innerHTML = (r.id && r.kind === "now" ? `<button class="btn big" data-rem="done">I've done it</button>` : "") +
    (r.id && r.kind !== "tomorrow" ? `<button class="btn ${r.kind === "now" ? "secondary" : "big"}" data-rem="snooze">Remind me in 10 minutes</button>` : "") +
    `<button class="btn ${r.id && r.kind !== "tomorrow" ? "quiet" : "big"}" data-rem="ok">${r.kind === "now" ? "Close" : "OK, thanks"}</button>`;
  box.hidden = false; $("#remBtns button").focus();
}
$("#remind").addEventListener("click", async (e) => {
  const b = e.target.closest("[data-rem]"); if (!b) return;
  const r = remQueue.shift();
  if (b.dataset.rem === "done") await markDone(r.id, true);
  if (b.dataset.rem === "snooze") { const s = store.get("lantern-snooze", {}); s[r.id] = Date.now() + 10 * 60000; store.set("lantern-snooze", s); toast("OK, I'll remind you again in 10 minutes."); }
  speechSynthesis?.cancel(); drawReminder();
});
const LEAD = { appointment: 60, event: 60, volunteer: 60, visit: 60, game: 30, plan: 30, special: 0 };
function reminderTick() {
  if (!S.profile) return;
  const mins = nowMins(), key = todayKey();
  const sent = store.get("lantern-sent-" + key, {}), snooze = store.get("lantern-snooze", {});
  let fired = false;
  for (const it of itemsFor().filter(remindable)) {
    if (isDone(it.id)) { delete snooze[it.id]; continue; }
    const until = minutesOf(it.time) - mins, detail = [fmtTime(it.time), it.detail].filter(Boolean).join(" · ");
    if (snooze[it.id]) {
      if (Date.now() >= snooze[it.id]) { delete snooze[it.id]; fired = true; showReminder({ id: it.id, kind: "now", when: "Reminder", title: it.title, detail, tag: it.id + ":snooze" }); }
      continue;
    }
    const lead = it.entry?.at_home ? 15 : LEAD[it.kind] ?? 15, lateLimit = it.kind === "medicine" ? -60 : -20;
    if (until <= lead && until > 0 && !sent[it.id + ":soon"]) {
      sent[it.id + ":soon"] = true; fired = true;
      showReminder({ id: it.id, kind: "soon", when: `Coming up ${untilText(until).toLowerCase()}`, title: it.title, detail, tag: it.id + ":soon" });
    } else if (until <= 0 && until > lateLimit && !sent[it.id + ":now"]) {
      sent[it.id + ":now"] = sent[it.id + ":soon"] = true; fired = true;
      showReminder({ id: it.id, kind: "now", when: "It's time", title: it.title, detail, tag: it.id + ":now" });
    }
  }
  // the day before a planned appointment or event: one friendly heads-up after waking up
  const wake = minutesOf(S.profile.wake || "08:00");
  if (mins >= wake) for (const it of itemsFor(addDays(key, 1)).filter((x) => x.entry && x.kind !== "special")) {
    if (sent[it.id + ":tomorrow"]) continue;
    sent[it.id + ":tomorrow"] = true; fired = true;
    showReminder({ id: it.id, kind: "tomorrow", when: "Tomorrow", title: it.title, detail: [it.allDay ? "All day" : fmtTime(it.time), it.detail].filter(Boolean).join(" · "), tag: it.id + ":tomorrow" });
  }
  store.set("lantern-sent-" + key, sent); store.set("lantern-snooze", snooze);
  refreshIfNeeded(fired);
}
let lastBoardSig = null;
function refreshIfNeeded(fired) {        // re-draw the home screen only when the board changes (home has no typing boxes)
  const sig = upcomingWindow().map((it) => it.id + untilText(it.diff)).join("|") + (nextItem()?.id || "") + todayKey();
  if ((fired || sig !== lastBoardSig) && screen === "home") render();
  lastBoardSig = sig;
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) { reminderTick(); autoRefresh(); } });
async function askNotify() {
  if (!("Notification" in window)) return toast("This browser can't show pop-up notifications. On-screen reminders still work.");
  const p = await Notification.requestPermission();
  toast(p === "granted" ? "Pop-up reminders are on." : "Pop-ups weren't allowed. On-screen reminders still work.");
  render();
}
function testReminder() {
  showReminder({ id: null, kind: "now", when: "This is a test", title: "Here's what a reminder looks like", detail: "You'll see this, hear a chime, and get a pop-up on your computer.", tag: "test" });
}

// ------------------------------------------------------------------ navigation
let navStack = [];
function go(name, opts = {}) {
  if (screen !== name && !opts.replace) navStack.push(screen);
  screen = name; render(); window.scrollTo(0, 0); main.focus({ preventScroll: true });
}
function back() { go(navStack.pop() || "home", { replace: true }); }
document.addEventListener("click", (e) => { const b = e.target.closest("[data-go]"); if (b) { e.preventDefault(); go(b.dataset.go); } });
$("#brandBtn").addEventListener("click", () => S.profile && go("home"));
function applySettings() {
  document.documentElement.dataset.size = S.settings.text_size || "M";
  document.documentElement.dataset.theme = S.settings.theme || "lantern";
  const fam = S.profile?.family, call = $("#callBtn");
  if (fam?.phone) { call.href = "tel:" + fam.phone.replace(/[^\d+]/g, ""); $("#callLabel").textContent = `Call ${fam.name || "family"}`; call.hidden = false; }
  else call.hidden = true;
  $("#bottombar").hidden = !S.profile || screen === "setup";
  $$("#bottombar [data-go]").forEach((b) => b.toggleAttribute("aria-current", b.dataset.go === screen));
}
function tickClock() {
  const d = new Date();
  $("#clock").textContent = d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" }) + ", " +
    d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}
const backBtn = () => `<button class="backlink" id="backBtn">${svg('<path d="m15 6-6 6 6 6"/>')}Back</button>`;
function render() {
  applySettings();
  const view = { home: viewHome, check: viewCheck, result: viewResult, read: viewRead, planner: viewPlanner, events: viewEvents,
    volunteer: viewVolunteer, news: viewNews, places: viewPlaces, weather: viewWeather, settings: viewSettings, setup: viewSetup }[screen] || viewHome;
  if (screen !== "read") stopCamera();
  view();
  $("#backBtn")?.addEventListener("click", back);
}

// ------------------------------------------------------------------ HOME
function greetingWord() { const h = new Date().getHours(); return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening"; }
const TILES = [
  ["check", "shield", "Check a message", "Is this text or email a scam?", "primary"],
  ["read", "read", "Read and magnify", "Recipes, labels and letters, made big", "t-read"],
  ["planner", "day", "My planner", "Medicines, appointments and plans", "t-plan"],
  ["events", "star", "Events", "Festivals, classes and things to do", "t-events"],
  ["volunteer", "hands", "Volunteer", "Ways to help that suit you", "t-vol"],
  ["news", "news", "Local news", "Stories about what you enjoy", "t-news"],
  ["places", "places", "Places to go", "Senior centers, libraries and more", "t-places"],
  ["weather", "sun", "Weather", "And what to wear today", "t-wx"],
];
function viewHome() {
  const win = upcomingWindow(6), todays = itemsFor(), allDay = todays.filter((it) => it.allDay);
  const later = nextItem(), conflicts = conflictsFor(todays);
  let board;
  if (win.length) {
    board = `<section class="board" aria-label="The next 6 hours">
      <div class="board-head"><h2>Coming up in the next 6 hours</h2><button class="btn quiet" data-go="planner">My whole day</button></div>
      <ol class="board-list">${win.map((it, i) => `
        <li class="bitem ${i === 0 ? "first" : ""} ${it.diff <= 0 ? "due" : ""} ${conflicts.some((c) => c.ids.includes(it.id)) ? "clash" : ""}">
          <div class="bwhen"><strong>${fmtTime(it.time)}</strong><span>${untilText(it.diff)}</span></div>
          <div class="bwhat"><span class="kind ${it.kind}">${KIND_LABEL[it.kind] || "Plan"}</span><strong>${esc(it.title)}</strong>${it.detail ? `<span class="bdetail">${esc(it.detail)}</span>` : ""}</div>
          <button class="btn ${i === 0 ? "" : "secondary"}" data-done="${esc(it.id)}">Done</button>
        </li>`).join("")}</ol></section>`;
  } else {
    board = `<section class="board calm" aria-label="The next 6 hours">
      <div class="board-head"><h2>Nothing in the next 6 hours</h2><button class="btn quiet" data-go="planner">My whole day</button></div>
      <p>${later ? `Later today: <strong>${esc(later.title)}</strong> at ${fmtTime(later.time)}.` : `You're all set for today. Enjoy your ${new Date().getHours() < 17 ? "day" : "evening"}.`}</p></section>`;
  }
  const notifAsk = "Notification" in window && Notification.permission === "default" && !store.get("lantern-notif-later", false)
    ? `<div class="banner"><div><strong>Turn on pop-up reminders?</strong><span>So I can remind you about medicines even when this window is hidden.</span></div>
       <div class="row"><button class="btn" id="notifYes">Turn on</button><button class="btn quiet" id="notifLater">Not now</button></div></div>` : "";
  main.innerHTML = `
    <section class="hero">
      <div><h1>${greetingWord()}, ${esc(S.profile.name)}.</h1>
        <p class="hero-date">${new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}${allDay.length ? ` · <strong>${allDay.map((it) => esc(it.title)).join(", ")}</strong>` : ""}</p></div>
      <button class="wxchip" id="wxchip" data-go="weather" hidden></button>
    </section>
    ${notifAsk}
    ${conflictBox(conflicts.filter((c) => !c.ids.every((id) => isDone(id))))}
    ${board}
    <h2 class="section-title">What would you like to do?</h2>
    <div class="tiles">${TILES.map(([go, ic, t, sub, cls]) => `
      <button class="tile ${cls}" data-go="${go}"><span class="ticon">${ICON[ic]}</span><div><strong>${t}</strong><span id="tile-${go}">${sub}</span></div></button>`).join("")}
    </div>`;
  $$("[data-done]", main).forEach((b) => b.addEventListener("click", () => markDone(b.dataset.done, true)));
  $("#notifYes")?.addEventListener("click", askNotify);
  $("#notifLater")?.addEventListener("click", () => { store.set("lantern-notif-later", true); render(); });
  loadWeather($("#wxchip"), "chip");
  homePreviews();
}
async function homePreviews() {          // small live hints under the buttons
  const week = []; for (let i = 0; i < 7; i++) week.push(...itemsFor(addDays(todayKey(), i)).filter((x) => x.entry && x.kind !== "special"));
  if (week.length) $("#tile-planner").textContent = `${week.length} plan${week.length > 1 ? "s" : ""} in the next 7 days`;
  const ev = await getSection("events").catch(() => null);
  if (screen !== "home" || !ev) return;
  const pick = (l) => [...(l || [])].sort((a, b) => a.date.localeCompare(b.date))[0];
  const next = pick(ev.items) || pick((ev.special || []).filter((x) => !/happening now/i.test(x.title)));
  if (next && $("#tile-events")) $("#tile-events").textContent = `Next: ${next.title.length > 42 ? next.title.slice(0, 40) + "…" : next.title} · ${shortDate(next.date)}`;
}

// ------------------------------------------------------------------ WEATHER
let wxCache = null, wxPending = null;
async function loadWeather(el, mode) {
  if (!el) return;
  if (!S.profile?.lat) {
    if (mode === "full") el.innerHTML = `<p>Add your town in Settings to see the weather.</p><button class="btn" data-go="setup">Add my town</button>`;
    return;
  }
  try {
    if (!wxCache || Date.now() - wxCache.t > 30 * 60000) {            // one request at a time, shared by every screen
      wxPending ??= api("/api/weather").finally(() => (wxPending = null));
      wxCache = { t: Date.now(), w: await wxPending };
    }
    const w = wxCache.w;
    el.hidden = false;
    if (mode === "chip") el.innerHTML = `${ICON.sun}<span><strong>${w.now.temp}°</strong> ${esc(cap(w.summary))}</span>`;
    else el.innerHTML = `
      <div class="wxbig"><div class="temp">${w.now.temp}°</div>
        <div><strong>${esc(cap(w.summary))}</strong><br>Feels like ${w.now.feels}°. High ${w.today.high}°, low ${w.today.low}°.<br>
        <span class="hint">Chance of rain: ${w.today.rain_chance}%. Wind up to ${w.today.wind} mph.</span></div></div>
      <h2 class="h3">What to wear</h2><ul class="tips">${w.advice.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>`;
  } catch (e) {
    console.warn("weather:", e.message);
    if (mode === "full") el.innerHTML = `<p>The weather isn't available right now. Check that this computer is connected to the internet, then come back to this page.</p>`;
  }
}
function viewWeather() {
  main.innerHTML = `${backBtn()}<h1>Weather</h1><p class="sub">${esc(S.profile.town || "")}</p>
    <section class="card" id="wxFull"><div class="row"><span class="spinner"></span> Getting the weather...</div></section>`;
  loadWeather($("#wxFull"), "full");
}

// ------------------------------------------------------------------ CHECK A MESSAGE
let sender = "unknown";
function viewCheck() {
  main.innerHTML = `${backBtn()}
    <h1>Check a message</h1>
    <p>Paste or type the text or email you're not sure about.</p>
    <label class="field" for="msg">The message</label>
    <textarea id="msg" class="input" placeholder="For example: Your package is on hold. Pay the $1.99 fee here...">${esc(lastCheck?.text || "")}</textarea>
    <div class="row" style="margin:.7rem 0 1.4rem">
      <button class="btn secondary" id="pasteBtn">Paste what I copied</button>
      <label class="btn secondary" for="shot">Use a screenshot instead</label>
      <input type="file" id="shot" accept="image/*" hidden>
      <button class="btn quiet" id="clearMsg">Clear the box</button>
      <span id="ocrStatus" class="hint" aria-live="polite"></span>
    </div>
    <h2 class="h3">Who sent it?</h2>
    <div class="choices" role="group" aria-label="Who sent it">
      ${[["known", "Someone in my contacts"], ["unknown", "A number or address I don't know"], ["not_sure", "I'm not sure"]]
        .map(([v, l]) => `<button class="choice" data-sender="${v}" aria-pressed="${sender === v}"><span class="dot"></span>${l}</button>`).join("")}
    </div>
    <button class="btn big" id="checkBtn" style="margin-top:1.4rem">Check this message</button>
    <div class="privacy">${ICON.lock}<span>Checked right here by Lantern Guide's own scam detector. Your message is never sent to any company.</span></div>`;
  $$("[data-sender]", main).forEach((b) => b.addEventListener("click", () => {
    sender = b.dataset.sender; $$("[data-sender]", main).forEach((x) => x.setAttribute("aria-pressed", x === b));
  }));
  $("#pasteBtn").addEventListener("click", async () => {
    try { $("#msg").value = await navigator.clipboard.readText(); $("#msg").focus(); }
    catch { toast("Press and hold in the box, then choose Paste."); $("#msg").focus(); }
  });
  $("#clearMsg").addEventListener("click", () => { $("#msg").value = ""; lastCheck = null; $("#msg").focus(); });
  $("#shot").addEventListener("change", async (e) => {
    const f = e.target.files[0]; if (!f) return;
    const text = await readImage(f, $("#ocrStatus")); e.target.value = "";
    if (text) { $("#msg").value = text; toast("Here's the text I found. Please check it looks right."); }
  });
  $("#checkBtn").addEventListener("click", runCheck);
}
async function runCheck() {
  const text = $("#msg").value.trim();
  if (!text) { toast("Type or paste a message first."); $("#msg").focus(); return; }
  const btn = $("#checkBtn"); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> Checking...';
  try { lastCheck = { text, sender, result: await api("/api/check", { text, sender }) }; go("result", { replace: true }); }
  catch (e) { toast(e.message, 7000); btn.disabled = false; btn.textContent = "Check this message"; }
}
const VERDICT = {
  scam: { h: "This looks like a scam", a: "Don't reply, tap any links, or call numbers in it. If you're worried, call the company using the number on your card or bill." },
  junk: { h: "This looks like junk", a: "Probably an advertisement. It's not dangerous, but don't tap links or reply. You can delete it." },
  unsure: { h: "I'm not sure about this one", a: "Before you reply or tap anything, check with someone you trust." },
  normal: { h: "This looks like a normal message", a: "Nothing here looks like a scam. If it ever asks for money or passwords later, check it again." },
  notice: { h: "This looks like a real account notice", a: "It seems to come from the real company, and it doesn't ask you for anything. Copycat scams look just like this, though, so check the safe way below." },
};
function viewResult() {
  if (!lastCheck?.result) return go("check", { replace: true });
  const r = lastCheck.result, v = VERDICT[r.verdict], fam = S.profile?.family;
  const famCall = fam?.phone ? `<a class="btn" style="background:var(--safe);border-color:var(--safe)" href="tel:${esc(fam.phone.replace(/[^\d+]/g, ""))}">Call ${esc(fam.name || "family")}</a>` : "";
  const familyTip = r.family_check ? `<p><strong>Tip:</strong> call that person on the number you already have for them, not the new one, to check it's really them.</p>` : "";
  main.innerHTML = `
    <section class="verdict ${r.verdict}" aria-live="polite">${ICON[r.verdict]}
      <h1>${v.h}</h1><p class="advice">${v.a}</p>${familyTip}</section>
    ${r.steps ? `<section class="card stepsbox">${ICON.shield}<div><h2 class="h3">The safe way to check</h2><p>${esc(r.steps)}</p></div></section>` : ""}
    ${r.reasons.length ? `<section class="card"><h2 class="h3">${r.verdict === "notice" ? "Worth knowing" : "Why I think so"}</h2><ul class="reasons">${r.reasons.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>` : ""}
    ${(r.good || []).length && ["normal", "notice"].includes(r.verdict) ? `<section class="card goodbox"><h2 class="h3">Why it looks OK</h2><ul class="reasons good">${r.good.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></section>` : ""}
    <div class="row" style="margin-top:1.2rem">
      <button class="btn secondary" id="sayBtn">Read this to me</button>${famCall}
      <button class="btn secondary" id="againBtn">Check another message</button>
    </div>
    <details style="margin-top:1.4rem"><summary class="hint">The message I checked</summary><div class="readout" style="margin-top:.6rem;font-size:.9rem">${esc(lastCheck.text)}</div></details>
    <p class="hint" style="margin-top:1rem">Lantern Guide can be wrong. When money, passwords or family emergencies are involved, always double-check with someone you trust.</p>`;
  $("#sayBtn").addEventListener("click", () => speak(`${v.h}. ${v.a} ${r.steps || ""} ${r.reasons.join(". ")}`));
  $("#againBtn").addEventListener("click", () => { lastCheck = null; go("check", { replace: true }); });
}

// ------------------------------------------------------------------ ON-DEVICE TEXT READING (Tesseract.js runs in this browser)
let tesseractReady = null;
function loadTesseract() {
  if (window.Tesseract) return Promise.resolve();
  tesseractReady ??= new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "https://cdn.jsdelivr.net/npm/tesseract.js@5.1.1/dist/tesseract.min.js";
    s.onload = ok; s.onerror = () => { tesseractReady = null; fail(new Error("Couldn't load the text reader. Check the internet connection.")); };
    document.head.appendChild(s);
  });
  return tesseractReady;
}
/* Reading words in a picture.
   1) On a Mac, Lantern Guide asks Apple's own text reader (the one behind Live Text) on this computer: very accurate.
   2) Otherwise, the reader built into this browser, run on two cleaned-up versions of the picture; the clearer result wins. */
let appleOCR = null;                      // null = not checked yet
async function toCanvas(src, zoom = 1, maxSide = 3000) {
  const img = src instanceof HTMLCanvasElement ? src : await createImageBitmap(src).catch(() => null);
  if (!img) return null;
  const sw = img.width / zoom, sh = img.height / zoom, sx = (img.width - sw) / 2, sy = (img.height - sh) / 2;
  const scale = Math.min(maxSide / Math.max(sw, sh), Math.max(1, 1800 / sw), 3), c = document.createElement("canvas");
  c.width = Math.round(sw * scale); c.height = Math.round(sh * scale);
  const g = c.getContext("2d", { willReadFrequently: true });
  g.imageSmoothingQuality = "high"; g.drawImage(img, sx, sy, sw, sh, 0, 0, c.width, c.height);
  return c;
}
const toBlob = (c, type = "image/jpeg", q = 0.92) => new Promise((ok) => c.toBlob(ok, type, q));
function grayStretch(c) {                // gray + contrast stretch
  const out = document.createElement("canvas"); out.width = c.width; out.height = c.height;
  const g = out.getContext("2d", { willReadFrequently: true }); g.drawImage(c, 0, 0);
  const px = g.getImageData(0, 0, c.width, c.height), d = px.data, hist = new Uint32Array(256);
  for (let i = 0; i < d.length; i += 4) { const y = (d[i] * 299 + d[i + 1] * 587 + d[i + 2] * 114) / 1000 | 0; d[i] = y; hist[y]++; }
  const total = d.length / 4; let lo = 0, hi = 255, acc = 0;
  for (; lo < 255 && (acc += hist[lo]) < total * 0.02; lo++);
  acc = 0; for (; hi > 0 && (acc += hist[hi]) < total * 0.02; hi--);
  const span = Math.max(30, hi - lo);
  for (let i = 0; i < d.length; i += 4) { const v = Math.max(0, Math.min(255, ((d[i] - lo) * 255) / span)); d[i] = d[i + 1] = d[i + 2] = v; }
  g.putImageData(px, 0, 0); return out;
}
function adaptive(c) {                   // black-and-white that copes with shadows and uneven light (Bradley's method)
  const w = c.width, h = c.height, g0 = grayStretch(c), g = g0.getContext("2d", { willReadFrequently: true });
  const px = g.getImageData(0, 0, w, h), d = px.data, I = new Float64Array((w + 1) * (h + 1)), s = Math.max(8, Math.round(w / 16));
  for (let y = 0; y < h; y++) { let row = 0; for (let x = 0; x < w; x++) { row += d[(y * w + x) * 4]; I[(y + 1) * (w + 1) + x + 1] = I[y * (w + 1) + x + 1] + row; } }
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) {
    const x1 = Math.max(0, x - s), x2 = Math.min(w - 1, x + s), y1 = Math.max(0, y - s), y2 = Math.min(h - 1, y + s);
    const area = (x2 - x1 + 1) * (y2 - y1 + 1);
    const sum = I[(y2 + 1) * (w + 1) + x2 + 1] - I[y1 * (w + 1) + x2 + 1] - I[(y2 + 1) * (w + 1) + x1] + I[y1 * (w + 1) + x1];
    const i = (y * w + x) * 4, v = d[i] * area < sum * 0.85 ? 0 : 255; d[i] = d[i + 1] = d[i + 2] = v;
  }
  g.putImageData(px, 0, 0); return g0;
}
async function readImage(src, statusEl, zoom = 1) {
  const busy = (t) => (statusEl.innerHTML = `<span class="spinner" style="display:inline-block;vertical-align:middle"></span> ${t}`);
  try {
    busy("Reading the picture...");
    const c = await toCanvas(src, zoom);
    if (!c) throw new Error("I couldn't open that picture. Try another one.");
    let text = "", conf = 0;
    if (appleOCR !== false) {
      try {
        const r = await fetch("/api/ocr", { method: "POST", headers: { "Content-Type": "image/jpeg" }, body: await toBlob(c) });
        const j = await r.json();
        if (j.available && !j.error) { appleOCR = true; text = j.text; conf = j.confidence; }
        else if (!j.available) appleOCR = false;
      } catch { appleOCR = false; }
    }
    if (appleOCR !== true) {
      busy("Loading the text reader...");
      await loadTesseract();
      let best = { text: "", confidence: -1 };
      for (const [i, variant] of [grayStretch, adaptive].entries()) {
        const { data } = await Tesseract.recognize(await toBlob(variant(c), "image/png"), "eng", {
          logger: (m) => { if (m.status === "recognizing text") statusEl.textContent = `Reading the picture (try ${i + 1} of 2)... ${Math.round(m.progress * 100)}%`; },
        });
        if (data.confidence > best.confidence) best = data;
        if (data.confidence > 85) break;
      }
      text = best.text; conf = best.confidence;
    }
    statusEl.textContent = "";
    text = (text || "").replace(/[ \t]+\n/g, "\n").replace(/\n{3,}/g, "\n\n").trim();
    if (!text) toast("I couldn't find any words. Try more light, and hold the paper so the words are sharp and fill the picture.", 7000);
    else if (conf < 55) toast("Some words may be wrong because the picture was blurry. Try more light, or hold steadier.", 7000);
    return text;
  } catch (e) { statusEl.textContent = ""; toast(e.message, 7000); return ""; }
}

// ------------------------------------------------------------------ READ AND MAGNIFY
let readSize = store.get("lantern-read-size", 1.6), camStream = null, readMode = "new", pages = 0;
function stopCamera() { camStream?.getTracks().forEach((t) => t.stop()); camStream = null; }
function viewRead() {
  stopCamera();
  main.innerHTML = `${backBtn()}
    <h1>Read and magnify</h1>
    <p>Take a photo of a recipe, medicine label or letter, and I'll show the words big and read them to you. Or use the magnifier like a magnifying glass.</p>
    <input type="file" id="photo" accept="image/*" capture="environment" hidden>
    <div class="row" id="readStart">
      <button class="btn big-ish" data-photo="new">${ICON.read}Take or choose a photo</button>
      <button class="btn secondary big-ish" id="magBtn">Use the magnifier</button>
    </div>
    <p id="readStatus" class="hint" aria-live="polite" style="margin-top:.6rem"></p>
    <div id="magArea"></div>
    <div id="readArea"></div>
    <div class="privacy">${ICON.lock}<span id="ocrNote">Photos are read right here and are never sent to any company.</span></div>`;
  api("/api/ocr_status").then((o) => { appleOCR = o.available ? true : false; const n = $("#ocrNote"); if (n) n.textContent = o.available
    ? "Photos are read by the computer running Lantern Guide, in your own home. They are never sent to any company."
    : "Photos are read right here on this device and are never sent to any company."; }).catch(() => {});
  $("#photo").addEventListener("change", async (e) => {
    const f = e.target.files[0]; e.target.value = ""; if (!f) return;
    const text = await readImage(f, $("#readStatus")); if (!text) return;
    addReading(text);
  });
  $("#magBtn").addEventListener("click", startMagnifier);
  showReadout();
}
document.addEventListener("click", photoClick);
function photoClick(e) {
  const b = e.target.closest("[data-photo]"); if (!b || screen !== "read") return;
  readMode = b.dataset.photo; $("#photo").click();
}
function addReading(text) {
  if (readMode === "add" && readText) { pages += 1; readText += `\n\n— Page ${pages} —\n\n` + text; toast(`Added page ${pages}.`); }
  else { readText = text; pages = 1; }
  readMode = "new"; showReadout();
  $("#readArea")?.scrollIntoView({ behavior: "smooth", block: "start" });
}
function showReadout() {
  const area = $("#readArea"); if (!area) return;
  $("#readStart").hidden = !!readText;
  if (!readText) { area.innerHTML = ""; return; }
  area.innerHTML = `
    <div class="readbar">
      <button class="btn" data-photo="new">${ICON.read}Read a new photo</button>
      <button class="btn secondary" data-photo="add">${ICON.plus}Add another page</button>
      <button class="btn secondary" id="magBtn2">Magnifier</button>
      <button class="btn quiet" id="clearRead">Clear and start over</button>
    </div>
    <label class="field" for="rsize" style="margin-top:1rem">Text size</label>
    <div class="sizerow"><span aria-hidden="true">A</span><input type="range" id="rsize" min="1" max="3.4" step="0.1" value="${readSize}"><span aria-hidden="true" style="font-size:1.6em">A</span></div>
    <div class="readout" id="readout" style="font-size:${readSize}rem">${esc(readText)}</div>
    <div class="row" style="margin-top:1rem"><button class="btn" id="readAloud">Read it to me</button><button class="btn secondary" id="stopRead">Stop reading</button>
    <button class="btn secondary" id="checkThis">Check this for a scam</button></div>
    <p class="hint" style="margin-top:.8rem">Reading from photos isn't perfect. For medicine doses, always confirm with the label or your pharmacist.</p>`;
  $("#rsize").addEventListener("input", (e) => { readSize = +e.target.value; $("#readout").style.fontSize = readSize + "rem"; store.set("lantern-read-size", readSize); });
  $("#readAloud").addEventListener("click", () => speak(readText));
  $("#stopRead").addEventListener("click", () => speechSynthesis?.cancel());
  $("#checkThis").addEventListener("click", () => { lastCheck = { text: readText }; go("check"); });
  $("#clearRead").addEventListener("click", () => { readText = ""; pages = 0; speechSynthesis?.cancel(); showReadout(); window.scrollTo(0, 0); });
  $("#magBtn2").addEventListener("click", startMagnifier);
}
async function startMagnifier() {
  if (!navigator.mediaDevices?.getUserMedia) return toast("The magnifier needs a camera this browser can use.", 7000);
  stopCamera();
  try { camStream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 }, height: { ideal: 1080 } } }); }
  catch { return toast("I couldn't use the camera. Allow camera access and try again.", 6000); }
  const track = camStream.getVideoTracks()[0], caps = track.getCapabilities?.() || {};
  $("#magArea").innerHTML = `
    <div class="magnifier" style="margin-top:1rem"><video id="cam" autoplay playsinline muted></video></div>
    <p class="hint" style="margin:.6rem 0 0">Tip: use good light and hold the paper still, about an arm's length from a laptop camera (closer with a phone). Zoom in so the words fill the box, then press <strong>Read the words</strong>.</p>
    <div class="sizerow" style="margin-top:.8rem"><span>Zoom</span><input type="range" id="zoom" min="1" max="5" step="0.1" value="2" aria-label="Zoom"></div>
    <div class="row" style="margin-top:.6rem">
      <button class="btn" id="freeze">Hold this picture</button>
      <button class="btn secondary" id="readFrame">${readText ? "Read these words (new)" : "Read the words"}</button>
      ${readText ? `<button class="btn secondary" id="addFrame">Add these words as a page</button>` : ""}
      ${caps.torch ? `<button class="btn secondary" id="torch" aria-pressed="false">Light on</button>` : ""}
      <button class="btn quiet" id="closeMag">Close magnifier</button></div>`;
  const v = $("#cam"); v.srcObject = camStream; v.style.transform = "scale(2)";
  $("#magArea").scrollIntoView({ behavior: "smooth", block: "start" });
  $("#zoom").addEventListener("input", (e) => (v.style.transform = `scale(${e.target.value})`));
  $("#freeze").addEventListener("click", (e) => { if (v.paused) { v.play(); e.target.textContent = "Hold this picture"; } else { v.pause(); e.target.textContent = "Start moving again"; } });
  $("#torch")?.addEventListener("click", async (e) => {
    const on = e.target.getAttribute("aria-pressed") !== "true";
    try { await track.applyConstraints({ advanced: [{ torch: on }] }); e.target.setAttribute("aria-pressed", on); e.target.textContent = on ? "Light off" : "Light on"; } catch {}
  });
  const grab = async (mode) => {
    v.pause();                                                      // a still picture reads much better than a moving one
    const c = document.createElement("canvas"); c.width = v.videoWidth; c.height = v.videoHeight; c.getContext("2d").drawImage(v, 0, 0);
    const zoom = +$("#zoom").value || 1;
    stopCamera(); $("#magArea").innerHTML = "";
    const text = await readImage(c, $("#readStatus"), Math.max(1, zoom * 0.85)); if (!text) return;
    readMode = mode; addReading(text);
  };
  $("#readFrame").addEventListener("click", () => grab("new"));
  $("#addFrame")?.addEventListener("click", () => grab("add"));
  $("#closeMag").addEventListener("click", () => { stopCamera(); $("#magArea").innerHTML = ""; });
}

// ------------------------------------------------------------------ MY PLANNER: today, any future day, and the months ahead
async function markDone(id, done, k = todayKey()) {
  try { S.done = (await api("/api/done", { id, date: k, done })).done; render(); if (done) toast("Done. Nice work!"); }
  catch (e) { toast(e.message); }
}
let plannerSel = null, monthOpen = false;
function plannedKeys() { return new Set((S.added_events || []).map((e) => e.date)); }
const KIND_ICON = {
  medicine: svg('<rect x="2.5" y="8.5" width="19" height="7" rx="3.5" transform="rotate(-35 12 12)"/><path d="m9.4 7.8 4.1 6.3"/>'),
  meal: svg('<path d="M7 3v8a2 2 0 0 0 2 2v8M5 3v5M9 3v5M17 3c-1.7 1-3 3.2-3 6.5V13h3v8"/>'),
  activity: svg('<circle cx="13" cy="4.5" r="1.8"/><path d="m9 21 2.5-6.5L14 17v4M8 11l3-4 3.5 1.5L17 12M11 7l-1.5 5"/>'),
  appointment: svg('<rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 10h17M8 3v4M16 3v4M12 13v4M10 15h4"/>'),
  event: svg('<path d="m12 3.5 2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>'),
  special: svg('<path d="m12 3.5 2.6 5.3 5.8.8-4.2 4.1 1 5.8L12 16.8l-5.2 2.7 1-5.8-4.2-4.1 5.8-.8z"/>'),
  volunteer: svg('<path d="M12 20s-7-4.4-7-10a4 4 0 0 1 7-2.6A4 4 0 0 1 19 10c0 5.6-7 10-7 10z"/>'),
  game: svg('<circle cx="12" cy="12" r="8.5"/><path d="M12 3.5v17M3.5 12h17M6 6c3 3 3 9 0 12M18 6c-3 3-3 9 0 12"/>'),
  visit: svg('<path d="M12 21s7-6.2 7-11.5A7 7 0 0 0 5 9.5C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>'),
  plan: svg('<path d="M5 12.5 9.5 17 19 7.5"/>'),
};
function weekStart(k) { const d = dateOf(k); d.setDate(d.getDate() - d.getDay()); return keyOf(d); }
function viewPlanner() {
  const today = todayKey(); plannerSel ??= today;
  const notif = "Notification" in window ? Notification.permission : "unsupported";
  const upcoming = (S.added_events || []).filter((e) => e.date >= today).sort((a, b) => (a.date + (parseLooseTime(a.time) || "")).localeCompare(b.date + (parseLooseTime(b.time) || "")));
  let groups = "", last = null;
  for (const e of upcoming.slice(0, 30)) {
    if (e.date !== last) { if (last) groups += "</ol></div>"; const d = dateOf(e.date); last = e.date;
      groups += `<div class="upgroup"><div class="datebadge"><span>${d.toLocaleDateString(undefined, { month: "short" })}</span><strong>${d.getDate()}</strong><em>${d.toLocaleDateString(undefined, { weekday: "short" })}</em></div><ol>`; }
    const t = parseLooseTime(e.time), kind = e.kind || "appointment";
    groups += `<li class="ag k-${kind}"><span class="ag-icon">${KIND_ICON[kind] || KIND_ICON.plan}</span>
      <div class="ag-body"><strong>${esc(e.title)}</strong><span>${t ? fmtTime(t) : "All day"}${e.place ? " · " + esc(e.place) : ""}</span></div>
      <button class="btn secondary" data-edit="${esc(e.id)}">Change</button></li>`;
  }
  if (last) groups += "</ol></div>";
  main.innerHTML = `${backBtn()}
    <div class="titlebar"><h1>My planner</h1><button class="btn" id="addPlan">${ICON.plus}Add an appointment or plan</button></div>
    <p class="sub">Add anything, any day ahead, like a doctor's appointment next month. I'll remind you the day before and again before it starts.</p>
    <div class="weekbar card">
      <div class="weekhead"><button class="cal-nav" id="wkPrev" aria-label="Previous week">${svg('<path d="m15 6-6 6 6 6"/>')}</button>
        <strong id="wkLabel"></strong>
        <button class="cal-nav" id="wkNext" aria-label="Next week">${svg('<path d="m9 6 6 6-6 6"/>')}</button></div>
      <div class="week" id="week"></div>
      <div class="row weekfoot"><button class="btn quiet" id="toToday">Today</button><button class="btn quiet" id="monthBtn" aria-expanded="${monthOpen}">${monthOpen ? "Hide the month" : "Show the whole month"}</button></div>
      <div id="monthBox" ${monthOpen ? "" : "hidden"}></div>
    </div>
    <section class="dayview" id="pday"></section>
    <section class="nsec"><div class="nsec-head"><h2>Coming up</h2><p>Everything you've added, in order.</p></div>
      ${upcoming.length ? `<div class="uplist2">${groups}</div>` : `<div class="card"><p style="margin:0">Nothing added yet. Press <strong>Add an appointment or plan</strong>, or add events from the Events and Volunteer pages.</p></div>`}
    </section>
    <details class="card extra" style="margin-top:1.6rem"><summary><strong>Reminder settings</strong></summary>
      <p style="margin-top:.8rem">While Lantern Guide is open (even in a hidden tab), I'll remind you before each medicine, activity and appointment with a pop-up and a soft chime. Meal reminders only come if you turned them on.</p>
      <div class="settings-list">
        <div class="setrow"><span>Computer pop-ups</span>${notif === "default" ? `<button class="btn" id="notifBtn">Turn on</button>` : notif === "granted" ? `<strong class="ok">On</strong>` : notif === "denied" ? `<span class="hint">Blocked by the browser. Click the icon left of the web address and allow notifications.</span>` : `<span class="hint">Not available here. On-screen reminders still work.</span>`}</div>
        <div class="setrow"><span>Spoken reminders</span><button class="switch" id="voiceBtn" role="switch" aria-checked="${store.get("lantern-voice", true)}"><span></span>${store.get("lantern-voice", true) ? "On" : "Off"}</button></div>
        <div class="setrow"><span>Chime sound</span><button class="switch" id="chimeBtn" role="switch" aria-checked="${store.get("lantern-chime", true)}"><span></span>${store.get("lantern-chime", true) ? "On" : "Off"}</button></div>
      </div>
      <button class="btn secondary" id="testRem" style="margin-top:1rem">Show me a test reminder</button></details>`;
  let wk = weekStart(plannerSel);
  const pick = (k) => { plannerSel = k; wk = weekStart(k); drawWeek(); drawDay(); if (monthOpen) drawMonth(); };
  function drawWeek() {
    const marks = plannedKeys();
    $("#wkLabel").textContent = `Week of ${dateOf(wk).toLocaleDateString(undefined, { month: innerWidth < 620 ? "short" : "long", day: "numeric" })}`;
    $("#week").innerHTML = [...Array(7)].map((_, i) => {
      const k = addDays(wk, i), d = dateOf(k), n = itemsFor(k).filter((x) => x.entry).length;
      return `<button class="wday${k === today ? " today" : ""}${k < today ? " past" : ""}" data-wk="${k}" aria-pressed="${k === plannerSel}" aria-label="${esc(fmtDate(k, { plain: true }))}${n ? `, ${n} plan${n > 1 ? "s" : ""}` : ""}">
        <span class="wd">${DAYS[d.getDay()]}</span><span class="num">${d.getDate()}</span><span class="dots">${"•".repeat(Math.min(n, 3))}</span></button>`;
    }).join("");
    $$("[data-wk]", main).forEach((b) => b.addEventListener("click", () => pick(b.dataset.wk)));
  }
  function drawMonth() { $("#monthBox").innerHTML = calendarHTML("pcal", { sel: plannerSel, view: plannerSel.slice(0, 7), marks: plannedKeys(), onPick: (k) => pick(k) }); }
  drawWeek(); drawDay(); if (monthOpen) drawMonth();
  $("#wkPrev").addEventListener("click", () => { wk = addDays(wk, -7); drawWeek(); });
  $("#wkNext").addEventListener("click", () => { wk = addDays(wk, 7); drawWeek(); });
  $("#toToday").addEventListener("click", () => pick(today));
  $("#monthBtn").addEventListener("click", (e) => { monthOpen = !monthOpen; $("#monthBox").hidden = !monthOpen; e.target.textContent = monthOpen ? "Hide the month" : "Show the whole month"; if (monthOpen) drawMonth(); });
  $("#addPlan").addEventListener("click", () => openPlanSheet({ date: plannerSel >= today ? plannerSel : today }));
  $$("[data-edit]", main).forEach((b) => b.addEventListener("click", () => openPlanSheet(S.added_events.find((e) => e.id === b.dataset.edit))));
  $("#notifBtn")?.addEventListener("click", askNotify);
  $("#voiceBtn").addEventListener("click", () => { store.set("lantern-voice", !store.get("lantern-voice", true)); render(); });
  $("#chimeBtn").addEventListener("click", () => { store.set("lantern-chime", !store.get("lantern-chime", true)); render(); });
  $("#testRem").addEventListener("click", testReminder);
}
function drawDay() {
  const k = plannerSel, today = todayKey(), items = itemsFor(k), mins = nowMins(), n = k === today ? nextItem() : null;
  const clashes = conflictsFor(items), real = items.filter((it) => it.kind !== "meal" || it.remind);
  const row = (it) => {
    const done = isDone(it.id, k), late = k === today && !it.allDay && minutesOf(it.time) <= mins && it.kind === "medicine" && !done;
    if (it.kind === "meal" && !it.remind) return `<li class="ag meal"><span class="ag-time">${fmtTime(it.time)}</span><span class="ag-meal">${KIND_ICON.meal}${esc(it.title)} <em>(usual time, no reminder)</em></span></li>`;
    return `<li class="ag k-${it.kind}${done ? " done" : ""}${late ? " late" : ""}${n && n.id === it.id ? " next" : ""}${clashes.some((c) => c.ids.includes(it.id)) ? " clash" : ""}">
      <span class="ag-time">${it.allDay ? "All day" : fmtTime(it.time)}${late ? "<em>Not done yet</em>" : n && n.id === it.id ? "<em>Next</em>" : ""}</span>
      <span class="ag-icon">${KIND_ICON[it.kind] || KIND_ICON.plan}</span>
      <div class="ag-body"><strong>${esc(it.title)}</strong>${it.detail ? `<span>${esc(it.detail)}</span>` : ""}<span class="ag-kind">${KIND_LABEL[it.kind] || "Plan"}</span></div>
      ${it.entry ? `<button class="btn secondary" data-editday="${esc(it.entry.id)}">Change</button>` :
        k === today ? `<button class="donepill" data-toggle="${esc(it.id)}" aria-pressed="${done}">${ICON.check}<span>${done ? "Done" : "Mark done"}</span></button>` : "<span></span>"}</li>`;
  };
  $("#pday").innerHTML = `
    <div class="dayhead"><h2>${esc(fmtDate(k))}</h2>${k >= today ? `<button class="btn secondary" id="addThisDay">${ICON.plus}Add on this day</button>` : ""}</div>
    ${conflictBox(clashes)}
    ${items.length ? `<ol class="agenda">${items.map(row).join("")}</ol>` : `<div class="card"><p style="margin:0">Nothing planned for this day.</p></div>`}
    ${!real.length && items.length ? `<p class="hint">Only your usual mealtimes so far.</p>` : ""}`;
  $$("[data-toggle]", $("#pday")).forEach((b) => b.addEventListener("click", () => markDone(b.dataset.toggle, b.getAttribute("aria-pressed") !== "true")));
  $$("[data-editday]", $("#pday")).forEach((b) => b.addEventListener("click", () => openPlanSheet(S.added_events.find((e) => e.id === b.dataset.editday))));
  $("#addThisDay")?.addEventListener("click", () => openPlanSheet({ date: k }));
}

const WATCHABLE = /\b(watch|movie|film|screening|cinema|matinee|game|vs\.?|playoffs?|super bowl|world series|tv show)\b/i;
// ------------------------------------------------------------------ the "add to my planner" sheet (also used by Events, Volunteer, Places)
function openPlanSheet(ev = {}) {
  const editing = !!(ev.id && (S.added_events || []).some((e) => e.id === ev.id));
  const d = { title: "", date: todayKey(), time: "", duration: 60, place: "", note: "", kind: "appointment", ...ev };
  if (d.date < todayKey()) d.date = todayKey();
  const t = parseLooseTime(d.time) || (!ev.title ? "10:00" : "");        // a brand-new appointment starts with a time set
  const box = $("#sheet");
  box.innerHTML = `<div class="sheetcard" role="dialog" aria-modal="true" aria-labelledby="sheetTitle">
    <div class="sheethead"><h2 id="sheetTitle">${editing ? "Change this plan" : "Add to my planner"}</h2><button class="btn quiet" data-sheet="close">Cancel</button></div>
    <label class="field" for="sh-what">What is it?</label>
    <input id="sh-what" class="input" value="${esc(d.title)}" placeholder="Doctor's appointment">
    ${editing || ev.title ? "" : `<div class="chips" style="margin-top:.6rem">${["Doctor's appointment", "Dentist", "Pharmacy pickup", "Lunch with a friend", "Haircut", "Family visit", "Watch a game", "See a movie"].map((x) => `<button type="button" class="chip" data-idea="${esc(x)}">${esc(x)}</button>`).join("")}</div>`}
    <div id="sh-watch"></div>
    <span class="field" style="margin-top:1.1rem">Which day?</span>
    <div class="chips" style="margin-bottom:.6rem"><button type="button" class="chip" data-quick="0">Today</button><button type="button" class="chip" data-quick="1">Tomorrow</button><button type="button" class="chip" data-quick="7">In a week</button></div>
    ${calendarHTML("shcal", { sel: d.date, view: d.date.slice(0, 7), marks: plannedKeys(), noPast: true, onPick: () => sheetCheck() })}
    <p class="hint" id="sh-daytext" style="margin:.4rem 0 0"></p>
    <span class="field" style="margin-top:1.1rem">What time?</span>
    ${timePicker("sh-time", t, { label: "Time", optional: true, toggleText: "It has a set time", fallback: "10:00" })}
    <div id="sh-len"><span class="field" style="margin-top:1rem">About how long?</span>
      <div class="chips">${[30, 60, 120, 180].map((m) => `<button type="button" class="chip" data-len="${m}" aria-pressed="${d.duration === m}">${durText(m)}</button>`).join("")}</div></div>
    <label class="field" for="sh-where" style="margin-top:1rem">Where? (optional)</label>
    <input id="sh-where" class="input" value="${esc(d.place)}" placeholder="Dr. Patel, 123 Main St">
    <label class="field" for="sh-note" style="margin-top:1rem">A note for yourself (optional)</label>
    <input id="sh-note" class="input" value="${esc(d.note)}" placeholder="Bring my insurance card">
    <div id="sh-clash"></div>
    <div class="row" style="margin-top:1.2rem"><button class="btn big" data-sheet="save">${editing ? "Save changes" : "Add to my planner"}</button></div>
    ${editing ? `<button class="btn quiet danger-text" data-sheet="delete" style="margin-top:.6rem">Remove this plan</button>` : ""}
  </div>`;
  box.hidden = false; document.body.classList.add("noscroll");
  drawPickers(box);
  let duration = d.duration;
  let watch = d.watch || "";                              // "home" | "other_home" | "in_person" (for games, movies, shows)
  const watchable = () => d.kind === "game" || WATCHABLE.test($("#sh-what")?.value || d.title);
  function drawWatch() {
    const el = $("#sh-watch"); if (!el) return;
    if (!watchable()) { el.innerHTML = ""; return; }
    const isGame = d.kind === "game" || /game|vs\.?|match/i.test($("#sh-what").value);
    const fam = S.profile?.family;
    el.innerHTML = `<div class="watchq">
      <span class="field" style="margin-top:1rem">How will you watch it?</span>
      <div class="choices two">
        <button type="button" class="choice" data-watch="tv" aria-pressed="${watch === "home" || watch === "other_home"}"><span class="dot"></span>${isGame ? "On TV" : "At home (TV or streaming)"}</button>
        <button type="button" class="choice" data-watch="in_person" aria-pressed="${watch === "in_person"}"><span class="dot"></span>In person${isGame ? " at the game" : " at the theater"}</button></div>
      ${watch === "home" || watch === "other_home" ? `<span class="field" style="margin-top:.9rem">Whose home?</span>
        <div class="choices two"><button type="button" class="choice" data-watch="home" aria-pressed="${watch === "home"}"><span class="dot"></span>My own home</button>
        <button type="button" class="choice" data-watch="other_home" aria-pressed="${watch === "other_home"}"><span class="dot"></span>A friend's or family member's home</button></div>` : ""}
      ${watch === "home" ? `<p class="hint" style="margin:.5rem 0 0">Great. Since you'll be home, I won't remind you to bring anything.${d.tv ? ` It's on <strong>${esc(d.tv)}</strong>.` : ""}</p>` : ""}
      ${watch === "in_person" ? `<div class="ticketbox">${d.venue ? `<p style="margin:0 0 .5rem"><strong>${esc(d.venue)}</strong></p>` : ""}
        <p style="margin:0 0 .6rem">Lantern Guide can't buy tickets for you, but ${d.tickets ? "this opens the ticket page, and " : ""}someone you trust can help.</p>
        <div class="row">${d.tickets ? `<a class="btn secondary sm" href="${esc(d.tickets)}" target="_blank" rel="noopener">See tickets</a>` : ""}
        ${fam?.phone ? `<a class="btn secondary sm" href="tel:${esc(fam.phone.replace(/[^\d+]/g, ""))}">${ICON.phone}Ask ${esc(fam.name || "family")} to help</a>` : ""}</div>
        <p class="hint" style="margin:.6rem 0 0">Buy tickets only from the official team, theater or box office. Be careful with tickets sold through texts or social media.</p></div>` : ""}
    </div>`;
  }
  function sheetCheck() {
    const date = calOpts.shcal.sel, time = getTime("sh-time");
    $("#sh-daytext").textContent = fmtDate(date);
    $("#sh-len").hidden = !time;
    drawWatch();
    const draftItem = { id: "draft", time, allDay: !time, kind: d.kind, title: $("#sh-what").value.trim() || "This plan", duration, entry: { at_home: watch === "home" } };
    const others = itemsFor(date).filter((x) => x.id !== `ev-${d.id}`);
    const clashes = time ? conflictsFor([...others, draftItem]).filter((c) => c.ids.includes("draft")) : [];
    $("#sh-clash").innerHTML = conflictBox(clashes);
  }
  sheetCheck();
  box.onclick = async (e) => {
    const idea = e.target.closest("[data-idea]"); if (idea) { $("#sh-what").value = idea.dataset.idea; if (/game/i.test(idea.dataset.idea)) duration = 180; sheetCheck(); return; }
    const wb = e.target.closest("[data-watch]");
    if (wb) { watch = wb.dataset.watch === "tv" ? (watch === "other_home" ? "other_home" : "home") : wb.dataset.watch; sheetCheck(); return; }
    const q = e.target.closest("[data-quick]");
    if (q) { const k = addDays(todayKey(), +q.dataset.quick); Object.assign(calOpts.shcal, { sel: k, view: k.slice(0, 7) }); redrawCal("shcal"); sheetCheck(); return; }
    const len = e.target.closest("[data-len]");
    if (len) { duration = +len.dataset.len; $$("[data-len]", box).forEach((x) => x.setAttribute("aria-pressed", x === len)); sheetCheck(); return; }
    const a = e.target.closest("[data-sheet]")?.dataset.sheet;
    if (e.target === box || a === "close") return closeSheet();
    if (a === "save") {
      const title = $("#sh-what").value.trim(); if (!title) { $("#sh-what").focus(); return toast("Type what it is first."); }
      if (watchable() && !watch) { $("#sh-watch").scrollIntoView({ behavior: "smooth", block: "center" }); return toast("First, tell me how you'll watch it."); }
      let place = $("#sh-where").value.trim();
      if (watchable()) place = watch === "home" ? `At home${d.tv ? ", on " + d.tv : ""}` : watch === "other_home" ? (place && !/^on /i.test(place) ? place : `At a friend's or family member's home${d.tv ? ", on " + d.tv : ""}`) : (d.venue || place);
      const entry = { ...d, id: d.id || "own" + Date.now(), title, date: calOpts.shcal.sel, time: getTime("sh-time"), duration,
        place, note: $("#sh-note").value.trim(), watch: watchable() ? watch : "", at_home: watchable() && watch === "home" };
      delete entry.onSaved;
      try { S.added_events = (await api("/api/add_event", { event: entry })).added_events; } catch (err) { return toast(err.message); }
      closeSheet(); toast(`${editing ? "Saved" : "Added"} for ${fmtDate(entry.date)}${entry.time ? " at " + fmtTime(entry.time) : ""}.`, 5000);
      ev.onSaved?.(entry); render();
    }
    if (a === "delete") {
      if (!confirm("Remove this plan?")) return;
      S.added_events = (await api("/api/add_event", { remove: d.id })).added_events; closeSheet(); toast("Removed."); render();
    }
  };
  if (box._tc) box.removeEventListener("timechange", box._tc);
  box._tc = sheetCheck; box.addEventListener("timechange", sheetCheck);
  $("#sh-what").addEventListener("input", sheetCheck);
  setTimeout(() => (d.title ? $("[data-sheet=save]", box) : $("#sh-what")).focus(), 50);
}
function closeSheet() { const b = $("#sheet"); b.hidden = true; b.innerHTML = ""; document.body.classList.remove("noscroll"); }
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#sheet").hidden) closeSheet(); });

// ------------------------------------------------------------------ EVENTS, VOLUNTEER, NEWS, PLACES (kept fresh automatically)
const secCache = {};
async function getSection(part, refresh = false) {
  const c = secCache[part];
  if (!refresh && c && Date.now() - c.t < 10 * 60000) return c.data;
  const data = await api(`/api/section?part=${part}${refresh ? "&refresh=1" : ""}`);
  if (!data.loading) secCache[part] = { t: Date.now(), data };      // still looking: ask again soon
  else delete secCache[part];
  return data;
}
let refreshTimer = null;
function autoRefresh() {                       // while a board page is open, look for new things every 30 minutes
  clearInterval(refreshTimer);
  if (!["events", "volunteer", "news", "places"].includes(screen)) return;
  refreshTimer = setInterval(() => { if (!document.hidden) { delete secCache[screen]; render(); } }, 30 * 60000);
}
let filterBy = {};
function boardShell(part, title, sub, icon) {
  main.innerHTML = `${backBtn()}
    <div class="titlebar"><h1><span class="ticon sm t-${part}">${ICON[icon]}</span>${title}</h1>
      <button class="btn secondary lookagain" id="lookAgain">${ICON.refresh}Look again</button></div>
    <p class="sub">${sub}</p>
    <div id="filters"></div>
    <div id="secBody"><div class="row" style="margin-top:1.2rem"><span class="spinner"></span> Looking around ${esc(S.profile.town || "your town")}...</div></div>
    <p class="hint" id="secUpdated" style="margin-top:1.4rem"></p>
    <div class="privacy">${ICON.lock}<span>Only stories from real news organizations are shown, and each one names its source. To search, only your town and interests are used, never your name, medicines or messages.</span></div>`;
  $("#lookAgain").addEventListener("click", () => loadSection(part, true));
  autoRefresh();
}
function problemBox(r, what) {
  return `<div class="card notice"><p style="margin:0"><strong>I couldn't look for ${what} just now.</strong> Check that this computer is connected to the internet, then press <strong>Look again</strong>.</p>
    ${r.details?.length ? `<details style="margin-top:.6rem"><summary class="hint">Details for a helper</summary><ul class="hint">${r.details.map((d) => `<li>${esc(d)}</li>`).join("")}</ul></details>` : ""}</div>`;
}
function filterChips(part, items, extra = []) {
  const present = new Set(items.flatMap((i) => i.interests || i.for || []));
  const opts = [...(S.profile.interests || []).filter((i) => present.has(i) && !(part === "volunteer" && /^volunteer/i.test(i))), ...extra];
  if (!opts.length) return "";
  const cur = filterBy[part] || "All";
  return `<div class="chips filters" role="group" aria-label="Show">${["All", ...opts].map((o) => `<button class="chip" data-filter="${esc(o)}" aria-pressed="${o === cur}">${esc(o)}</button>`).join("")}</div>`;
}
function wireFilters(part) {
  $$("[data-filter]").forEach((b) => b.addEventListener("click", () => { filterBy[part] = b.dataset.filter; loadSection(part); }));
}
const passes = (part, it) => { const f = filterBy[part]; return !f || f === "All" || (it.interests || it.for || []).includes(f) || (f === "Special days" && it.kind === "special") || (f === "Public meetings" && it.meeting) || (f === "Open calls" && !it.date); };
const sourceLine = (it) => `<span class="src">${ICON.check}${esc(it.outlets?.length > 1 ? `Reported by ${it.outlets.slice(0, 3).join(", ")}` : `From ${it.source}`)}${it.published ? ` · ${esc(shortDate(it.published))}` : ""}</span>`;
const interestTags = (it) => (it.interests || []).map((i) => `<span class="tag for">For you: ${esc(i)}</span>`).join("");
const addedIds = () => new Set((S.added_events || []).map((e) => e.id));
function addButton(it, kind) {
  const done = addedIds().has(it.id);
  return `<button class="btn ${done ? "secondary" : ""}" data-add="${esc(it.id)}" data-kind="${kind}" ${done ? "disabled" : ""}>${done ? `${ICON.check}In my planner` : `${ICON.plus}Add to my planner`}</button>`;
}
function wireAdd(list) {
  $$("[data-add]", main).forEach((b) => b.addEventListener("click", () => {
    const it = list.find((x) => x.id === b.dataset.add);
    const game = b.dataset.kind === "game";
    openPlanSheet({ id: it.id, title: it.title, date: it.date, time: it.time || "", kind: b.dataset.kind, place: game ? "" : it.place || "",
      venue: game ? it.place : "", tv: it.tv || "", tickets: it.tickets || "",
      note: "", url: it.url || "", duration: game ? 180 : 120,
      onSaved: () => { b.disabled = true; b.classList.add("secondary"); b.innerHTML = `${ICON.check}In my planner`; } });
  }));
}
function groupByDate(items, render1) {
  let out = "", last = null;
  for (const it of items) {
    if (it.date !== last) { out += `<h3 class="dategroup">${esc(fmtDate(it.date))}</h3>`; last = it.date; }
    out += render1(it);
  }
  return out;
}
async function loadSection(part, refresh = false) {
  const body = $("#secBody"); if (!body) return;
  if (refresh) body.innerHTML = `<div class="row" style="margin-top:1.2rem"><span class="spinner"></span> Looking for new things...</div>`;
  let r;
  try { r = await getSection(part, refresh); } catch (e) { body.innerHTML = problemBox({ details: [e.message] }, "that"); return; }
  if (screen !== part) return;
  clearTimeout(loadSection._poll);
  if (r.loading) loadSection._poll = setTimeout(() => { if (screen === part) loadSection(part); }, 3000);
  if (r.status === "setup") { body.innerHTML = `<div class="card"><p>${esc(r.message)}</p><button class="btn" data-go="setup">Add my town</button></div>`; return; }
  $("#secUpdated").textContent = r.updated ? `Last checked at ${r.updated}. I look again by myself every so often.` : "";
  // Only redraw when something really changed, so an open "More" box or the reading spot doesn't jump while it keeps looking
  const { updated, loading, ...rest } = r;
  const sig = part + JSON.stringify(rest) + JSON.stringify(filterBy[part] || "");
  if (refresh || sig !== loadSection._sig || body.dataset.drawn !== "1") {      // a new visit to the page always draws
    const keyOf = (d) => d.dataset.key || (d.querySelector("summary")?.textContent || "").trim();
    const open = new Set($$("details[open]", body).map(keyOf)), y = window.scrollY;
    ({ events: drawEvents, volunteer: drawVolunteer, news: drawNews, places: drawPlaces })[part](r, body);
    wireFilters(part);
    $$("details", body).forEach((d) => { if (open.has(keyOf(d))) d.open = true; });
    if (!refresh) window.scrollTo(0, y);
    loadSection._sig = sig; body.dataset.drawn = "1";
  }
  body.querySelector(":scope > .stillbar")?.remove();
  if (r.loading) body.insertAdjacentHTML("afterbegin", `<div class="stillbar"><span class="spinner"></span><span>Still looking for more near ${esc(S.profile.town)}. New things will appear here by themselves.</span></div>`);
}
function viewEvents() {
  boardShell("events", "Events", `Things to do in and around ${esc(S.profile.town)}, picked for your interests.`, "star");
  loadSection("events");
}
function eventCard(it) {
  if (it.kind === "special") {
    const done = addedIds().has(it.id);
    return `
    <details class="specialrow${it.your_faith ? " faith" : ""}" data-key="sp-${esc(it.id)}"><summary><span class="tag">${it.your_faith ? "Your faith" : "Special day"}</span><strong>${esc(it.title)}</strong><span class="more">More</span></summary>
      <p class="why">${esc(it.why)}</p>
      <div class="row"><button class="btn secondary sm" data-add="${esc(it.id)}" data-kind="special" ${done ? "disabled" : ""}>${done ? `${ICON.check}In my planner` : `${ICON.plus}Add to my planner`}</button>
      ${it.phone ? `<a class="btn quiet" href="tel:${esc(it.phone.replace(/\D/g, ""))}">${ICON.phone}Call ${esc(it.phone)}</a>` : ""}</div></details>`;
  }
  if (it.kind === "game") return `
    <article class="event game"><div class="date">${it.time ? esc(fmtTime(it.time)) : "Time to be announced"}</div>
      <h3>${esc(it.title)}</h3>${it.summary ? `<p class="why">${esc(it.summary)}</p>` : ""}
      <div class="tagrow">${interestTags(it)}<span class="tag">${it.home ? "Home game" : "Away game"}</span></div>
      <span class="src">${ICON.check}Schedule from ESPN</span>
      <div class="row">${addButton(it, "game")}</div></article>`;
  if (it.listed) return `
    <article class="event">
      <div class="date">${it.time ? esc(fmtTime(it.time)) : "Time: see the listing"}${it.place ? ` · <span class="place">${esc(it.place)}</span>` : ""}</div>
      <h3>${esc(it.title)}</h3>
      ${it.summary ? `<p class="why">${esc(it.summary.length > 180 ? it.summary.slice(0, 177).replace(/\s\S*$/, "") + "…" : it.summary)}</p>` : ""}
      <div class="tagrow">${interestTags(it)}</div>
      <span class="src">${ICON.check}Listed on Eventbrite${it.organizer ? ` by ${esc(it.organizer)}` : ""}</span>
      <div class="row">${addButton(it, "event")}${it.url ? `<a class="btn quiet" href="${esc(it.url)}" target="_blank" rel="noopener">See the listing</a>` : ""}</div>
    </article>`;
  if (it.official) return `
    <article class="event official">
      <div class="date">${it.time ? esc(fmtTime(it.time)) : "All day"}${it.place ? ` · <span class="place">${esc(it.place)}</span>` : ""}</div>
      <h3>${esc(it.title)}</h3>
      ${it.summary ? `<p class="why">${esc(it.summary)}</p>` : ""}
      <div class="tagrow">${it.meeting ? `<span class="tag meet">Public meeting</span>` : ""}${interestTags(it)}</div>
      <span class="src">${ICON.check}From the calendar of ${esc(it.source.replace(" (official website)", ""))}${/official/.test(it.source) ? " (official)" : ""}</span>
      <div class="row">${addButton(it, it.meeting ? "plan" : "event")}${it.url ? `<a class="btn quiet" href="${esc(it.url)}" target="_blank" rel="noopener">More details</a>` : ""}</div>
    </article>`;
  return `
    <article class="event">
      <div class="date">${it.time ? esc(fmtTime(it.time)) : "Time: see the article"}</div>
      <h3>${esc(it.title)}</h3>
      ${it.summary ? `<p class="why">${esc(it.summary)}</p>` : ""}
      <div class="tagrow">${interestTags(it)}${it.smart ? `<span class="tag">Found by smart search</span>` : ""}</div>
      ${sourceLine(it)}
      <div class="row">${addButton(it, "event")}<a class="btn quiet" href="${esc(it.url)}" target="_blank" rel="noopener">Read the article</a></div>
    </article>`;
}
function drawEvents(r, body) {
  const soon = addDays(todayKey(), 45);
  const all = [...r.items, ...r.special.filter((x) => x.your_faith || x.date <= soon)].sort((a, b) => a.date.localeCompare(b.date) || (a.kind === "special") - (b.kind === "special"));
  $("#filters").innerHTML = filterChips("events", all, [...(all.some((i) => i.meeting) ? ["Public meetings"] : []), ...(r.special.length ? ["Special days"] : [])]);
  const shown = all.filter((it) => passes("events", it));
  const list = shown.filter((it) => it.kind === "special" || it.good_fit !== false), others = shown.filter((it) => it.kind !== "special" && it.good_fit === false);
  const near = list.filter((it) => it.kind === "special" || it.tier === "near"), games = list.filter((it) => it.kind === "game"), far = list.filter((it) => it.tier === "state");
  const worship = list.filter((it) => it.tier === "worship");
  const sec = (title, sub, items) => items.length ? `<section class="nsec first"><div class="nsec-head"><h2>${title}</h2>${sub ? `<p>${sub}</p>` : ""}</div><div class="events">${groupByDate(items, eventCard)}</div></section>` : "";
  body.innerHTML = (r.problem && !r.items.length ? problemBox(r, "local events") : "") +
    sec(`At ${esc(worship[0]?.source || "your place of worship")}`, "From your place of worship's own calendar.", worship) +
    sec(`Near you`, r.area?.length > 1 ? `In and around ${esc(r.area.slice(0, 4).join(", "))}. Special days are the short rows; tap one for more.` : "", near) +
    sec("Your teams' games", "Add a game to your planner and I'll remind you before it starts.", games) +
    sec(`Elsewhere in ${esc(r.state || "your state")}`, "A little farther away, but they match what you enjoy.", far) +
    (others.length ? `<details class="moreopps"><summary class="btn secondary">Other events nearby (${others.length})</summary>
      <p class="hint">These didn't look like as good a fit, but you might still enjoy some.</p><div class="events">${groupByDate(others, eventCard)}</div></details>` : "") +
    (!list.length ? `<div class="card"><p style="margin:0">${filterBy.events && filterBy.events !== "All" ? "Nothing for this one right now. Try another, or press Look again later." : "No upcoming events found right now. I'll keep looking."}</p></div>` : "");
  wireAdd(all);
}
function viewVolunteer() {
  boardShell("volunteer", "Volunteer", `Ways to help near ${esc(S.profile.town)}, matched to what you enjoy.`, "hands");
  loadSection("volunteer");
}
function placeCard(p, volunteer) {
  return `<article class="place-card">
      <span class="tag">${esc(p.label)}</span>
      <h3>${esc(p.name)}</h3>
      <p class="why"><strong>${p.miles} miles away</strong>${p.address ? " · " + esc(p.address) : ""}<br>${esc(volunteer && p.help ? p.help : p.why)}${p.hours ? `<br><span class="hint">Hours: ${esc(p.hours)}</span>` : ""}</p>
      <div class="tagrow">${(p.for || []).map((i) => `<span class="tag for">For you: ${esc(i)}</span>`).join("")}</div>
      <div class="row">
        ${p.phone ? `<a class="btn" href="tel:${esc(p.phone.replace(/[^\d+]/g, ""))}">${ICON.phone}${volunteer ? "Call to ask about helping" : "Call to ask what's on"}</a>` : ""}
        <button class="btn secondary" data-visit="${esc(p.id)}">${ICON.plus}Plan a visit</button>
        <button class="btn quiet" data-map="${esc(p.id)}" aria-expanded="false">Show map</button></div>
      <div class="mapbox" id="map-${esc(p.id)}" hidden></div>
    </article>`;
}
function wirePlaces(list, body) {
  $$("[data-map]", body).forEach((b) => b.addEventListener("click", () => {
    const p = list.find((x) => x.id === b.dataset.map), box = $("#map-" + p.id), open = box.hidden;
    if (open && !box.innerHTML) {
      const d = 0.006;
      box.innerHTML = `<iframe title="Map of ${esc(p.name)}" loading="lazy" src="https://www.openstreetmap.org/export/embed.html?bbox=${p.lon - d},${p.lat - d * 0.7},${p.lon + d},${p.lat + d * 0.7}&layer=mapnik&marker=${p.lat},${p.lon}"></iframe>`;
    }
    box.hidden = !open; b.textContent = open ? "Hide map" : "Show map"; b.setAttribute("aria-expanded", open);
  }));
  $$("[data-visit]", body).forEach((b) => b.addEventListener("click", () => {
    const p = list.find((x) => x.id === b.dataset.visit);
    openPlanSheet({ title: `Visit ${p.name}`, kind: "visit", place: [p.name, p.address].filter(Boolean).join(", "), duration: 60 });
  }));
}
function drawVolunteer(r, body) {
  const items = r.items || [], places = r.places || [], opps = r.opps || [], groups = r.groups || [];
  const has = (g) => g.programs.length + g.places.length + g.opps.length + g.stories.length;
  $("#filters").innerHTML = groups.length ? `<div class="chips jump" role="group" aria-label="Jump to a kind of volunteering">
    ${groups.map((g) => `<button class="chip" data-jump="vg-${g.cat}">${esc(g.title)}${g.opps.length + g.places.length ? ` <span class="n">${g.opps.length + g.places.length}</span>` : ""}</button>`).join("")}</div>` : "";
  const story = (it) => `
    <article class="event">
      <div class="date">${it.date ? `${esc(fmtDate(it.date))}${it.time ? ", " + esc(fmtTime(it.time)) : ""}` : "Looking for volunteers now"}</div>
      <h3>${esc(it.title)}</h3>${it.summary ? `<p class="why">${esc(it.summary)}</p>` : ""}
      <div class="tagrow">${interestTags(it)}</div>${sourceLine(it)}
      <div class="row">${it.date ? addButton(it, "volunteer") : ""}<a class="btn ${it.date ? "quiet" : "secondary"}" href="${esc(it.url)}" target="_blank" rel="noopener">Read the article</a></div>
    </article>`;
  const when = (o) => o.start ? `${esc(fmtDate(o.start))}${o.end && o.end !== o.start ? ` to ${esc(shortDate(o.end))}` : ""}` :
    esc(o.when_text || (o.recurring ? "Ongoing" : "Flexible")) + (o.commitment ? `<br><span class="hint">${esc(o.commitment)}</span>` : "");
  const table = (rows) => `<div class="oppwrap"><table class="opps"><colgroup><col class="c-title"><col class="c-org"><col class="c-when"><col class="c-due"><col class="c-good"><col class="c-link"></colgroup><thead><tr><th scope="col">Opportunity</th><th scope="col">Organization</th><th scope="col">When</th>
        <th scope="col">Apply by</th><th scope="col">Who can help</th><th scope="col"><span class="sr">Links</span></th></tr></thead><tbody>
      ${rows.map((o) => `<tr>
        <td data-label="Opportunity"><strong>${esc(o.title)}</strong>${o.summary ? `<span class="hint">${esc(o.summary.length > 120 ? o.summary.slice(0, 117).replace(/\s\S*$/, "") + "…" : o.summary)}</span>` : ""}
          <div class="tagrow">${interestTags(o)}${o.where !== "In person" ? `<span class="tag">${o.where === "Remote" ? "From home" : esc(o.where)}</span>` : ""}</div></td>
        <td data-label="Organization">${esc(o.org || "Listed on Idealist")}</td>
        <td data-label="When">${when(o)}</td>
        <td data-label="Apply by">${o.deadline ? `<strong class="due">${esc(shortDate(o.deadline))}</strong>` : `<span class="hint">No deadline listed</span>`}</td>
        <td data-label="Who can help">${o.senior_friendly ? `<span class="tag for">Welcomes 55+</span>` : ""}${o.good_for ? `<span class="hint">${esc(o.good_for)}</span>` : o.age ? `<span class="hint">Ages ${esc(o.age)}</span>` : `<span class="hint">Adults</span>`}</td>
        <td data-label="">${o.start ? `<button class="btn secondary sm" data-addopp="${esc(o.id)}" aria-label="Add to my planner">${ICON.plus}Add</button>` : ""}
          <a class="btn sm" href="${esc(o.url)}" target="_blank" rel="noopener">Details and apply</a></td></tr>`).join("")}
      </tbody></table></div>`;
  const prog = (p) => `<article class="place-card prog">
      <h3>${esc(p.title)}</h3><p class="hint" style="margin:-.1rem 0 .3rem">${esc(p.org)}</p>
      <p class="why">${esc(p.what)}<br><span class="hint">${esc(p.who)}</span></p>
      <p class="hint" style="margin-bottom:.6rem">${esc(p.how)}</p>
      <div class="row">${p.url ? `<a class="btn secondary" href="${esc(p.url)}" target="_blank" rel="noopener">Learn how to join</a>` : ""}
        ${p.phone ? `<a class="btn quiet" href="tel:${esc(p.phone.replace(/\D/g, ""))}">${ICON.phone}${esc(p.phone)}</a>` : ""}</div></article>`;
  const w = r.worship || {};
  const worshipBox = w.name ? `<section class="nsec first"><div class="nsec-head"><h2>At ${esc(w.name)}</h2><p>Your place of worship.</p></div>
      <div class="place-card mine"><p class="why">${w.address ? esc(w.address) : ""}</p>
        ${(r.worship_events || []).length ? `<ul class="minilist">${r.worship_events.map((e) => `<li><strong>${esc(e.title)}</strong> <span class="hint">${esc(fmtDate(e.date))}${e.time ? ", " + esc(fmtTime(e.time)) : ""}</span></li>`).join("")}</ul>` :
          `<p class="hint">Ask the office about ways to help: meals, events, visiting members and more.</p>`}
        <div class="row">${w.phone ? `<a class="btn" href="tel:${esc(w.phone.replace(/[^\d+]/g, ""))}">${ICON.phone}Call to ask about helping</a>` : ""}</div></div></section>` : "";
  const section = (g) => `<section class="nsec first volgroup" id="vg-${g.cat}">
      <div class="nsec-head"><h2>${esc(g.title)}</h2><p>${esc(g.about)}</p></div>
      ${g.opps.length ? `<h3 class="subhead">Openings near you</h3>${table(g.opps)}` : ""}
      ${g.places.length ? `<h3 class="subhead">Places near you that welcome helpers</h3><div class="places">${g.places.map((p) => placeCard(p, true)).join("")}</div>` : ""}
      ${g.programs.length ? `<h3 class="subhead">Programs you can join</h3><div class="places">${g.programs.map(prog).join("")}</div>` : ""}
      ${g.stories.length ? `<h3 class="subhead">In the local news</h3><div class="events">${g.stories.map(story).join("")}</div>` : ""}
    </section>`;
  body.innerHTML = (r.problem ? problemBox(r, "volunteering") : "") + worshipBox +
    `<p class="hint" style="margin:.4rem 0 0">Every idea here suits older adults. Your interests come first. Openings are checked every few hours.</p>` +
    groups.filter(has).map(section).join("") +
    (opps.length ? `<p class="hint" style="margin-top:1rem">Openings come from Idealist.org (VolunteerMatch is now part of it). Jobs, internships and roles for students are left out. Always check details with the organization.</p>` : "") +
    (!groups.some(has) && !r.problem ? `<div class="card"><p style="margin:0">Nothing found right now. I'll keep looking.</p></div>` : "");
  wireAdd(items); wirePlaces(places, body);
  $$("[data-jump]", $("#filters")).forEach((b) => b.addEventListener("click", () => document.getElementById(b.dataset.jump)?.scrollIntoView({ behavior: "smooth", block: "start" })));
  $$("[data-addopp]", body).forEach((b) => b.addEventListener("click", () => {
    const o = opps.find((x) => x.id === b.dataset.addopp);
    openPlanSheet({ id: o.id, title: `Volunteer: ${o.title}`, date: o.start, kind: "volunteer", place: o.org, note: "", duration: 120,
      onSaved: () => { b.disabled = true; b.innerHTML = `${ICON.check}In my planner`; } });
  }));
}
function viewNews() {
  boardShell("news", "Local news", `Stories from around ${esc(S.profile.town)} and about the things you enjoy. Upsetting crime and accident stories are left out.`, "news");
  loadSection("news");
}
function newsItem(n) {
  return `<article class="newsitem"><h3><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a></h3>
    ${n.summary ? `<p>${esc(n.summary)}</p>` : ""}<div class="tagrow">${n.group === "near" ? interestTags(n) : ""}</div>${sourceLine(n)}</article>`;
}
function drawNews(r, body) {
  const sports = r.sports || [];
  $("#filters").innerHTML = filterChips("news", [...r.items, ...sports.map((s) => ({ interests: [s.interest] }))]);
  const f = filterBy.news && filterBy.news !== "All" ? filterBy.news : null;
  const near = r.items.filter((n) => n.group === "near" && (!f || n.interests.includes(f)));
  const groups = (r.interests || []).filter((i) => !f || i === f).map((i) => [i, r.items.filter((n) => n.group === i)]).filter(([, l]) => l.length);
  const teamCards = sports.filter((s) => !f || s.interest === f).map((s) => `
    <section class="teamcard">
      <div class="teamhead"><span class="ticon sm t-events">${KIND_ICON.game}</span><div><h3>${esc(s.team)}</h3><span class="hint">${esc(s.league)}</span></div></div>
      <div class="teamgrid">
        <div><span class="lbl">Last game</span><strong>${s.last ? esc(s.last.text) : "No recent games"}</strong>${s.last ? `<span class="hint">${esc(shortDate(s.last.date))}</span>` : ""}</div>
        <div><span class="lbl">Next game</span>${s.next ? `<strong>${esc(s.next.title.replace(s.team + " ", ""))}</strong><span class="hint">${esc(fmtDate(s.next.date))}${s.next.time ? ", " + esc(fmtTime(s.next.time)) : ""}${s.next.tv ? " · " + esc(s.next.tv) : ""}</span>` : `<strong>Not scheduled yet</strong>`}</div>
      </div>
      ${s.next ? `<button class="btn secondary" data-game="${esc(s.next.id)}">${ICON.plus}Add the next game to my planner</button>` : ""}
      ${s.articles.length ? `<div class="newslist" style="margin-top:.9rem">${s.articles.map(newsItem).join("")}</div>` : ""}
    </section>`).join("");
  body.innerHTML = (r.problem ? problemBox(r, "local news") : "") +
    (near.length ? `<section class="nsec first"><div class="nsec-head"><h2>Near you</h2>${r.area?.length > 1 ? `<p>From ${esc(r.area.slice(0, 4).join(", "))}.</p>` : ""}</div><div class="newslist">${near.map(newsItem).join("")}</div></section>` : "") +
    (teamCards ? `<section class="nsec first"><div class="nsec-head"><h2>Your teams</h2><p>Scores, the next game and the latest news.</p></div>${teamCards}</section>` : "") +
    groups.map(([i, l]) => `<section class="nsec first"><div class="nsec-head"><h2>${esc(i)}</h2></div><div class="newslist">${l.map(newsItem).join("")}</div></section>`).join("") +
    (!near.length && !groups.length && !teamCards && !r.problem ? `<div class="card"><p style="margin:0">No new stories right now. I'll keep looking.</p></div>` : "");
  $$("[data-game]", body).forEach((b) => b.addEventListener("click", () => {
    const s = sports.find((x) => x.next?.id === b.dataset.game), g = s.next;
    openPlanSheet({ id: g.id, title: g.title, date: g.date, time: g.time, kind: "game", venue: g.place, tv: g.tv, tickets: g.tickets || "", duration: 180,
      onSaved: () => { b.disabled = true; b.innerHTML = `${ICON.check}In my planner`; } });
  }));
}
function viewPlaces() {
  boardShell("places", "Places to go", `Places near ${esc(S.profile.town)} that run activities, plus places that fit your interests.`, "places");
  loadSection("places");
}
const PLURAL = { "Senior center": "Senior centers", Library: "Libraries", "Community center": "Community centers", "Community garden": "Community gardens",
  Park: "Parks", "Place of worship": "Places of worship", "Arts center or museum": "Arts centers and museums", Theater: "Theaters",
  "Fitness or recreation center": "Fitness and recreation centers", "Food bank or community kitchen": "Food banks and community kitchens",
  "Animal shelter": "Animal shelters", "Charity shop": "Charity shops", Hospital: "Hospitals" };
function drawPlaces(r, body) {
  $("#filters").innerHTML = filterChips("places", r.items);
  const list = r.items.filter((it) => passes("places", it));
  let html = "", last = null;
  for (const p of list) {
    const base = p.label.split(" (")[0];
    if (base !== last) { last = base; html += `<h3 class="dategroup">${esc(PLURAL[base] || base)}</h3>`; }
    html += placeCard(p, false);
  }
  body.innerHTML = (r.problem ? problemBox(r, "places") : "") + (list.length ? `<div class="places">${html}</div>` : r.problem ? "" : `<div class="card"><p style="margin:0">I didn't find places nearby on the map yet.</p></div>`);
  wirePlaces(r.items, body);
}

// ------------------------------------------------------------------ SETTINGS
function loadQR() {
  if (window.qrcode) return Promise.resolve();
  return new Promise((ok, fail) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/qrcode-generator/1.4.4/qrcode.min.js"; s.onload = ok; s.onerror = fail;
    document.head.appendChild(s);
  });
}
async function viewSettings() {
  const st = S.settings;
  main.innerHTML = `${backBtn()}
    <h1>Settings</h1>
    <section class="card"><h2 class="h3">Text size</h2>
      <div class="chips">${[["S", "Small"], ["M", "Medium"], ["L", "Large"]].map(([v, l]) => `<button class="chip" data-size="${v}" aria-pressed="${st.text_size === v}">${l}</button>`).join("")}</div></section>
    <section class="card" style="margin-top:1rem"><h2 class="h3">Colors</h2>
      <p class="hint" style="margin-top:-.3rem">Pick whichever is easiest on your eyes. All of them are easy to read.</p>
      <div class="themes">${THEMES.map(([v, l, a, b]) => `<button class="theme-pick" data-theme-pick="${v}" aria-pressed="${(st.theme || "lantern") === v}">
        <span class="sw" style="background:${b}"><span style="background:${a}"></span></span>${l}</button>`).join("")}</div></section>
    <section class="card" style="margin-top:1rem"><h2 class="h3">My answers</h2>
      <p>Your name, town, interests, activities, family contact, medicines and meal times.</p>
      <button class="btn" data-go="setup">Change my answers</button></section>
    <section class="card" style="margin-top:1rem" id="phoneCard" hidden></section>
    <section class="card" style="margin-top:1rem"><h2 class="h3">Your privacy</h2>
      <ul class="reasons">
        <li>Messages you check are judged by Lantern Guide's own scam detector, on this computer. They're never sent to any company.</li>
        <li>Photos and screenshots are read inside your browser and never uploaded.</li>
        <li>Your answers, medicines and plans are saved only on this computer.</li>
        <li>To find events, news, volunteering and places, only your town and interests are used in searches.</li>
        <li>Only stories from real news organizations are shown. Press releases, social media and listing sites are left out.</li>
      </ul></section>
    <details class="card extra" style="margin-top:1rem"><summary><strong>Optional extra: smart event search</strong><span class="hint"> (for a family member)</span></summary>
      <p style="margin-top:.8rem">The Events page already works without this. With a free Google Gemini key, Lantern Guide also asks Gemini to search the web for dated events. Get a key at aistudio.google.com. ${st.has_key ? "<strong>A key is saved.</strong>" : ""}</p>
      <label class="field" for="gkey">Gemini API key</label><input id="gkey" class="input" type="password" autocomplete="off" placeholder="${st.has_key ? "Saved (paste a new one to replace)" : "Paste key here"}">
      <label class="field" for="gmodel" style="margin-top:.7rem">Gemini model</label><input id="gmodel" class="input" value="${esc(st.gemini_model || "gemini-2.5-flash")}">
      <div class="row" style="margin-top:.8rem"><button class="btn" id="saveKey">Save</button>${st.has_key ? `<button class="btn secondary" id="delKey">Remove key</button>` : ""}</div></details>
    <section class="card" style="margin-top:1rem"><h2 class="h3">Scam detector</h2><p id="modelStatus">Checking...</p></section>
    <section class="card" style="margin-top:1rem"><h2 class="h3">Start over</h2>
      <p>Erase all answers and plans and start the setup again.</p><button class="btn danger" id="resetBtn">Erase everything</button></section>`;
  $$("[data-size]", main).forEach((b) => b.addEventListener("click", async () => { S = await api("/api/settings", { text_size: b.dataset.size }); render(); }));
  $$("[data-theme-pick]", main).forEach((b) => b.addEventListener("click", async () => { S = await api("/api/settings", { theme: b.dataset.themePick }); render(); }));
  $("#saveKey").addEventListener("click", async () => {
    const body = { gemini_model: $("#gmodel").value.trim() || "gemini-2.5-flash" };
    if ($("#gkey").value.trim()) body.gemini_key = $("#gkey").value.trim();
    S = await api("/api/settings", body); delete secCache.events; toast("Saved."); render();
  });
  $("#delKey")?.addEventListener("click", async () => { S = await api("/api/settings", { gemini_key: "" }); delete secCache.events; toast("Key removed."); render(); });
  $("#resetBtn").addEventListener("click", async () => {
    if (!confirm("Erase all your answers and plans? This can't be undone.")) return;
    await api("/api/reset", {}); await boot();
  });
  api("/api/model").then((m) => {
    if (!$("#modelStatus")) return;                       // they've already left this page
    $("#modelStatus").textContent = m.ok ? `Ready (${m.model}).${m.notes.length ? " Note: " + m.notes.join(" ") : ""}` : `Not set up yet: copy the model files into the "models" folder. (${m.error})`;
  }).catch((e) => { if ($("#modelStatus")) $("#modelStatus").textContent = e.message; });
  try {
    const ph = await api("/api/phone"), card = $("#phoneCard"); if (!card) return;
    if (ph.on_phone) card.innerHTML = `<h2 class="h3">Use on your phone</h2><p><strong>You're using Lantern Guide on your phone.</strong> To keep it one tap away, use your browser's Share or menu button, then <strong>Add to Home Screen</strong>.</p>`;
    else if (!ph.enabled) { card.remove(); return; }      // the phone link isn't turned on: nothing to show here
    else {
      card.innerHTML = `<h2 class="h3">Use on your phone</h2>
        <div class="phonerow"><div id="qr" class="qr" aria-label="QR code for your phone"></div>
        <ol class="steps"><li>Open the <strong>Camera</strong> on your phone and point it at this code. Tap the link that appears.</li>
          <li>Your phone may say the connection isn't private, because this link only works in your home. Choose <strong>Show Details</strong> or <strong>Advanced</strong>, then continue to the website.</li>
          <li>Use the Share or menu button, then <strong>Add to Home Screen</strong>, so Lantern Guide is one tap away.</li></ol></div>
        <p class="hint">Phone and computer must be on the same Wi-Fi, and Lantern Guide must be running here. The code includes a private key, so only phones you scan it with can open your information.</p>`;
      await loadQR(); const q = qrcode(0, "M"); q.addData(ph.url); q.make();
      $("#qr").innerHTML = q.createSvgTag({ cellSize: 6, margin: 2, scalable: true });
    }
    card.hidden = false;
  } catch {}
}

// ------------------------------------------------------------------ SETUP (one friendly question at a time)
// Order: things that are fun and quick first (size, name, town, interests, activities, family); medicines and meals at the end.
const INTERESTS = ["Gardening", "Music", "Faith and worship", "Festivals and culture", "Volunteering", "Cooking", "Reading and the library",
  "Walking and nature", "Arts and crafts", "Games and cards", "Exercise classes", "Learning new things", "Grandchildren and family"];
const ACT_IDEAS = ["Morning walk", "Exercise class", "Worship service", "Call family", "Bingo", "Gardening", "Coffee with friends"];
let draft = null, step = 0;
const FAITH_RE = /faith|worship|church|temple|mosque|masjid|synagogue|gurdwara|mandir|parish|religio|christian|catholic|hindu|muslim|islam|jewish|sikh|buddh|jain/i;
const wantsFaith = () => (draft?.interests || []).some((i) => FAITH_RE.test(i));
const stepsList = () => ["welcome", "size", "name", "town", "interests", ...(wantsFaith() ? ["faith"] : []), "acts", "family", "sleep", "meals", "meds", "finish"];
const TRADITIONS = [["christian", "Christian"], ["catholic", "Catholic"], ["orthodox", "Orthodox Christian"], ["jewish", "Jewish"], ["muslim", "Muslim"],
  ["hindu", "Hindu"], ["sikh", "Sikh"], ["buddhist", "Buddhist"], ["jain", "Jain"], ["other", "Something else"], ["", "I'd rather not say"]];
function blankProfile() {
  return { name: "", town: "", wake: "07:00", bed: "22:00", meals: { breakfast: "08:00", lunch: "12:30", dinner: "18:00" },
    medications: [], activities: [], interests: [], family: { name: "", phone: "" } };
}
function viewSetup() {
  if (!draft) { draft = JSON.parse(JSON.stringify(S.profile || blankProfile())); if (S.profile && step === 0) step = 1; }
  const STEPS = stepsList(); step = Math.min(step, STEPS.length - 1);
  const k = STEPS[step], pct = Math.round((step / (STEPS.length - 1)) * 100);
  const nav = (nextLabel = "Next", skip = false) => `<div class="row wiznav">
    ${step > 0 ? `<button class="btn secondary" id="back">Back</button>` : ""}<button class="btn" id="next">${nextLabel}</button>
    ${skip ? `<button class="btn quiet" id="skip">Skip for now</button>` : ""}</div>`;
  let body = "";
  if (k === "welcome") body = `
    <img src="/lantern.svg" alt="" width="84" height="84" class="welcome-lamp"><h1>Hello, I'm Lantern Guide.</h1>
    <p>I can help you spot scam messages, read small print, remember your medicines and appointments, and find nice things to do nearby.</p>
    <p>First, a few easy questions so I can get to know you. It takes about five minutes, and you can change anything later.</p>${nav("Let's begin")}`;
  if (k === "size") body = `<h1>How big should the words be?</h1><p>Choose what's most comfortable to read. You can change it any time.</p>
    <div class="choices">${[["S", "Small"], ["M", "Medium"], ["L", "Large"]].map(([v, l]) => `<button class="choice" data-psize="${v}" aria-pressed="${S.settings.text_size === v}"><span class="dot"></span>${l}</button>`).join("")}</div>${nav()}`;
  if (k === "name") body = `<h1>What should I call you?</h1><label class="field" for="f-name">Your name</label>
    <input id="f-name" class="input" value="${esc(draft.name)}" autocomplete="given-name">${nav()}`;
  if (k === "town") body = `<h1>What town do you live in?</h1><p>I use it for the weather and to find events, news and places near you.</p>
    <label class="field" for="f-town">Town and state</label><input id="f-town" class="input" value="${esc(draft.town)}" placeholder="For example: Springfield, Illinois">
    <button class="btn secondary" id="findTown" style="margin-top:.7rem">Find my town</button><div id="townResults" class="choices" style="margin-top:.8rem"></div>${nav("Next", true)}`;
  if (k === "interests") {
    const own = draft.interests.filter((x) => !INTERESTS.includes(x));
    body = `<h1>What do you enjoy?</h1>
    <p>I'll look for events, news, volunteering and places for <strong>every</strong> interest you add.</p>
    <h2 class="h3">Some ideas</h2><p class="hint" style="margin-top:-.3rem">Tap any that fit you. These are only ideas.</p>
    <div class="chips ideas">${INTERESTS.map((x) => `<button class="chip pick" data-int="${esc(x)}" aria-pressed="${draft.interests.includes(x)}"><span class="mark" aria-hidden="true"></span>${esc(x)}</button>`).join("")}</div>
    <h2 class="h3" style="margin-top:1.6rem">Add your own, as many as you like</h2>
    <p class="hint" style="margin-top:-.3rem">Anything at all: travel, jazz, bird watching, knitting, golf, old movies, the Carolina Hurricanes...</p>
    <div class="addrow"><input id="f-own" class="input" placeholder="Type an interest" enterkeyhint="done" aria-label="Your own interest"><button class="btn" id="ownAdd">${ICON.plus}Add</button></div>
    <div class="chips" id="ownList" style="margin-top:.7rem">${own.map((x) => `<button class="chip own" data-rmint="${esc(x)}" aria-label="Remove ${esc(x)}">${esc(x)} <span aria-hidden="true">✕</span></button>`).join("")}</div>
    <p class="picked" id="pickedCount"></p>${nav()}`;
  }
  if (k === "faith") {
    draft.faith ??= { tradition: "", place: null };
    const f = draft.faith, pl = f.place;
    body = `<h1>Tell me about your faith community</h1>
    <div class="whybox">${ICON.lock}<div><strong>Why I ask</strong><p>So I can show your holy days, and events and ways to help at your own place of worship. This stays only on this computer and is never shared.</p></div></div>
    <h2 class="h3">Which faith?</h2>
    <div class="chips">${TRADITIONS.map(([v, l]) => `<button type="button" class="chip" data-trad="${v}" aria-pressed="${f.tradition === v && (v || f.answered)}">${l}</button>`).join("")}</div>
    ${f.tradition === "other" ? `<label class="field" for="f-tother" style="margin-top:.8rem">Which one?</label><input id="f-tother" class="input" value="${esc(f.other || "")}">` : ""}
    <h2 class="h3" style="margin-top:1.6rem">Where do you worship? <span class="hint">(optional)</span></h2>
    ${pl ? `<div class="card chosen"><div><strong>${esc(pl.name)}</strong><br><span class="hint">${esc(pl.address || "")}${pl.website ? " · has a website" : ""}</span></div>
        <button class="btn secondary" id="placeChange">Change</button></div>` : `
      <p class="hint" style="margin-top:-.3rem">Type its name, like "St. Mary's Church", "Beth Shalom" or "Hindu temple", then press Find.</p>
      <div class="addrow"><input id="f-wq" class="input" placeholder="Name of your church, temple, mosque..." aria-label="Name of your place of worship" enterkeyhint="search">
        <button class="btn" id="placeFind">Find</button></div>
      <button class="btn quiet" id="placeNear" style="padding-left:0">Show the nearest ${f.tradition && !["other", ""].includes(f.tradition) ? esc((TRADITIONS.find((x) => x[0] === f.tradition) || ["", ""])[1].toLowerCase()) + " " : ""}places of worship</button>
      <div id="placeResults" class="choices" style="margin-top:.8rem"></div>`}
    ${nav("Next", true)}`;
  }
  if (k === "acts") body = `<h1>What do you do regularly?</h1><p>Walks, classes, worship, calls with family. I'll put them in your planner and remind you.</p>
    <div class="listed">${draft.activities.map((a, i) => `<div class="card"><div><strong>${esc(a.name)}</strong><br><span class="hint">${a.days.map((d) => DAYS[d]).join(", ")} at ${fmtTime(a.time)}</span></div><button class="btn secondary" data-delact="${i}">Remove</button></div>`).join("")}</div>
    <section class="card stack" style="margin-top:1rem"><h2 class="h3">Add an activity</h2>
      <div><label class="field" for="a-name">What is it?</label><input id="a-name" class="input" placeholder="Type anything, for example: Morning walk">
        <p class="hint" style="margin:.5rem 0 .3rem">Or tap an idea to fill it in:</p>
        <div class="chips">${ACT_IDEAS.map((x) => `<button class="chip" data-idea="${esc(x)}">${esc(x)}</button>`).join("")}</div></div>
      <div><span class="field">Which days?</span><div class="days">${DAYS.map((d, i) => `<button class="chip" data-day="${i}" aria-pressed="false">${d}</button>`).join("")}</div>
        <button class="btn quiet" id="everyday" style="padding-left:0">Every day</button></div>
      <div><span class="field">What time?</span>${timePicker("a-time", "09:00", { label: "Activity time" })}</div>
      <button class="btn" id="a-add">${ICON.plus}Add this activity</button></section>${nav(draft.activities.length ? "Next" : "Skip for now")}`;
  if (k === "family") body = `<h1>Who should you call if something seems wrong?</h1><p>A "Call" button for them will always be at the bottom of the screen.</p>
    <label class="field" for="f-fname">Their name</label><input id="f-fname" class="input" value="${esc(draft.family.name)}" placeholder="Priya">
    <label class="field" for="f-fphone" style="margin-top:.7rem">Their phone number</label><input id="f-fphone" class="input" type="tel" value="${esc(draft.family.phone)}" placeholder="919-555-0123">${nav("Next", true)}`;
  if (k === "sleep") body = `<h1>When do you usually wake up and go to bed?</h1>
    <p>Press the arrows to change the time. I won't remind you about anything while you're asleep.</p>
    <div class="grid2"><div><span class="field">I wake up at</span>${timePicker("f-wake", draft.wake || "07:00", { label: "Wake up time" })}</div>
    <div><span class="field">I go to bed at</span>${timePicker("f-bed", draft.bed || "22:00", { label: "Bed time" })}</div></div>${nav()}`;
  if (k === "meals") {
    draft.meal_remind ??= {}; draft.meal_skip ??= {};
    body = `<h1>When do you usually eat?</h1>
    <div class="whybox">${ICON.day}<div><strong>Why I ask</strong><p>Knowing your mealtimes helps me plan your day. I can line up medicines you take with food, and let you know if an appointment falls at a mealtime. Meal reminders are up to you.</p></div></div>
    <div class="meal-list">${[["breakfast", "Breakfast", "08:00"], ["lunch", "Lunch", "12:30"], ["dinner", "Dinner", "18:00"]].map(([m, l, d]) => {
      const skip = !!draft.meal_skip[m];
      return `<div class="card mealcard ${skip ? "skipped" : ""}">
        <div class="mealhead"><h2 class="h3">${l}</h2>
          <div class="seg" role="group" aria-label="${l}"><button type="button" data-eat="${m}" aria-pressed="${!skip}">I usually eat ${l.toLowerCase()}</button><button type="button" data-skip="${m}" aria-pressed="${skip}">I usually skip it</button></div></div>
        ${skip ? `<p class="hint" style="margin:.6rem 0 0">No problem. I won't plan around ${l.toLowerCase()}.</p>` : `
        <span class="field" style="margin-top:.8rem">Around what time?</span>${timePicker("f-" + m, draft.meals[m] || d, { label: l + " time" })}
        <button type="button" class="tp-toggle" data-mrem="${m}" aria-pressed="${!!draft.meal_remind[m]}" style="margin-top:.8rem"><span class="box"></span>Remind me when it's time for ${l.toLowerCase()}</button>`}
      </div>`; }).join("")}</div>${nav()}`;
  }
  if (k === "meds") {
    const quick = [["breakfast", "With breakfast"], ["lunch", "With lunch"], ["dinner", "With dinner"]].filter(([m]) => draft.meals[m])
      .map(([m, l]) => [draft.meals[m], `${l} (${fmtTime(draft.meals[m])})`]);
    if (draft.bed) quick.push([toHHMM(Math.max(0, minutesOf(draft.bed) - 30)), `At bedtime (${fmtTime(toHHMM(Math.max(0, minutesOf(draft.bed) - 30)))})`]);
    body = `<h1>Last question: do you take any medicines?</h1><p>I'll remind you when it's time. Add each one, with when you take it.</p>
    <div class="listed">${draft.medications.map((m, i) => `<div class="card"><div><strong>${esc(m.name)}</strong>${m.dose ? ", " + esc(m.dose) : ""}<br><span class="hint">${m.times.map(fmtTime).join(" and ")}${m.with_food ? ", with food" : ""}</span></div><button class="btn secondary" data-delmed="${i}">Remove</button></div>`).join("")}</div>
    <section class="card stack" style="margin-top:1rem"><h2 class="h3">Add a medicine</h2>
      <div><label class="field" for="m-name">Name</label><input id="m-name" class="input" placeholder="Lisinopril"></div>
      <div><label class="field" for="m-dose">How much</label><input id="m-dose" class="input" placeholder="1 pill, 10 mg"></div>
      <div><span class="field">When do you take it?</span>
        ${quick.length ? `<div class="chips" style="margin-bottom:.6rem">${quick.map(([t, l]) => `<button type="button" class="chip" data-qt="${t}">${esc(l)}</button>`).join("")}</div>` : ""}
        ${timePicker("m-time", "08:00", { label: "Medicine time" })}
        <div id="m-times" class="chips" style="margin-top:.6rem"></div>
        <button class="btn secondary" id="m-addtime" style="margin-top:.6rem">${ICON.plus}I take it at another time too</button></div>
      <button class="choice" id="m-food" aria-pressed="false"><span class="dot"></span>Take with food</button>
      <button class="btn" id="m-add">${ICON.plus}Add this medicine</button></section>${nav(draft.medications.length ? "Next" : "I don't take any")}`;
  }
  if (k === "finish") {
    S.profile = draft; const items = itemsFor().filter((it) => it.kind !== "meal");
    body = `<h1>You're all set, ${esc(draft.name)}.</h1><p>Here's what today looks like. You can change anything in Settings.</p>
      <ol class="timeline">${items.length ? items.map((it) => `<li class="titem" style="grid-template-columns:5.2rem 1fr"><time>${it.allDay ? "All day" : fmtTime(it.time)}</time><div class="what"><strong>${esc(it.title)}</strong>${it.detail ? `<span>${esc(it.detail)}</span>` : ""}</div></li>`).join("") : "<li class='card'>Nothing planned yet.</li>"}</ol>
      ${nav("Go to my home screen")}`;
  }
  main.innerHTML = `<div class="wizard"><div class="progress" aria-label="Setup progress ${pct}%"><span style="width:${pct}%"></span></div>
    <p class="stepcount">Step ${step + 1} of ${STEPS.length}</p>${body}</div>`;
  drawPickers(main);
  wireSetup(k);
}
function wireSetup(k) {
  const next = async () => {
    if (k === "name") { draft.name = $("#f-name").value.trim(); if (!draft.name) return toast("Please type your name."); }
    if (k === "town") { const t = $("#f-town").value.trim(); if (t !== draft.town) { draft.town = t; delete draft.lat; delete draft.lon; } if (t && !draft.lat) await findTown(true); }
    if (k === "sleep") { draft.wake = getTime("f-wake"); draft.bed = getTime("f-bed"); }
    if (k === "meals") saveMeals();
    if (k === "interests") addOwn();
    if (k === "faith" && $("#f-tother")) draft.faith.other = $("#f-tother").value.trim();
    if (k === "family") draft.family = { name: $("#f-fname").value.trim(), phone: $("#f-fphone").value.trim() };
    if (k === "acts" && $("#a-name").value.trim()) return toast('Press "Add this activity" first, or clear the box.');
    if (k === "meds" && $("#m-name").value.trim()) return toast('Press "Add this medicine" first, or clear the box.');
    if (k === "finish") {
      if (next.busy) return;
      next.busy = true; const nb = $("#next"); if (nb) { nb.disabled = true; nb.textContent = "Saving..."; }
      try { S = await api("/api/profile", { profile: draft }); } catch (e) { next.busy = false; if (nb) { nb.disabled = false; nb.textContent = "Go to my home screen"; } return toast(e.message); } draft = null; step = 0; wxCache = null;
      Object.keys(secCache).forEach((x) => delete secCache[x]); navStack = []; go("home", { replace: true }); return;
    }
    step++; render(); window.scrollTo(0, 0);
  };
  $("#next")?.addEventListener("click", next);
  $("#skip")?.addEventListener("click", () => { step++; render(); });
  $("#back")?.addEventListener("click", () => { step--; render(); });
  if (!["interests"].includes(k)) main.querySelector("input")?.focus();
  if (k === "town") $("#findTown").addEventListener("click", () => findTown(false));
  function saveMeals() {
    ["breakfast", "lunch", "dinner"].forEach((m) => { draft.meals[m] = draft.meal_skip?.[m] ? "" : (getTime("f-" + m) || draft.meals[m] || ""); });
  }
  if (k === "meals") {
    $$("[data-eat],[data-skip]", main).forEach((b) => b.addEventListener("click", () => {
      saveMeals(); const m = b.dataset.eat || b.dataset.skip; draft.meal_skip[m] = !!b.dataset.skip;
      if (b.dataset.eat && !draft.meals[m]) draft.meals[m] = { breakfast: "08:00", lunch: "12:30", dinner: "18:00" }[m];
      if (b.dataset.skip) draft.meal_remind[m] = false;
      saveMeals(); render();
    }));
    $$("[data-mrem]", main).forEach((b) => b.addEventListener("click", () => {
      const m = b.dataset.mrem; draft.meal_remind[m] = !draft.meal_remind[m]; b.setAttribute("aria-pressed", draft.meal_remind[m]);
    }));
  }
  if (k === "faith") {
    const f = draft.faith;
    $$("[data-trad]", main).forEach((b) => b.addEventListener("click", () => {
      f.tradition = b.dataset.trad; f.answered = true; if ($("#f-tother")) f.other = $("#f-tother").value; render();
    }));
    $("#placeChange")?.addEventListener("click", () => { f.place = null; render(); });
    const findPlace = async () => {
      const q = $("#f-wq").value.trim();
      const box = $("#placeResults"); box.innerHTML = `<div class="row"><span class="spinner"></span> Looking near ${esc(draft.town || "you")}... This takes a few seconds.</div>`;
      try {
        const { results } = await api(`/api/findplace?q=${encodeURIComponent(q)}&trad=${encodeURIComponent(f.tradition || "")}${draft.lat ? `&lat=${draft.lat}&lon=${draft.lon}` : ""}`);
        // Not every place is on the map, so they can always keep the name they typed
        const own = q ? `<button class="choice" data-plown="1"><span class="dot"></span><span><strong>${results.length ? "None of these. " : ""}Save "${esc(q)}" as my place</strong><br><span class="hint">I'll still look for its events and news by name.</span></span></button>` : "";
        if (!results.length) { box.innerHTML = `<p>I couldn't find that on the map. Try one word of the name (like "Michael" or "Beth"), press <strong>Show the nearest</strong>, or just save the name.</p>` + own; }
        else box.innerHTML = `<p class="hint" style="margin:0">${q ? "Is it one of these? Tap it." : "The nearest ones to you. Tap yours."}</p>` +
          results.map((r, i) => `<button class="choice" data-pl="${i}"><span class="dot"></span><span><strong>${esc(r.name)}</strong><br><span class="hint">${esc(r.address)}${r.miles != null ? ` · ${r.miles} miles` : ""}</span></span></button>`).join("") + own;
        $$("[data-pl]", box).forEach((b) => b.addEventListener("click", () => { f.place = results[+b.dataset.pl]; render(); }));
        $("[data-plown]", box)?.addEventListener("click", () => { f.place = { name: q, address: "", website: "", phone: "" }; render(); });
      } catch (e) { box.innerHTML = `<p>Couldn't search right now (${esc(e.message)}). You can skip this and add it later.</p>`; }
    };
    $("#placeFind")?.addEventListener("click", findPlace);
    $("#placeNear")?.addEventListener("click", () => { $("#f-wq").value = ""; findPlace(); });
    $("#f-wq")?.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); findPlace(); } });
  }
  if (k === "size") $$("[data-psize]", main).forEach((b) => b.addEventListener("click", async () => { S.settings = (await api("/api/settings", { text_size: b.dataset.psize })).settings; render(); }));
  function addOwn() {
    const box = $("#f-own"); if (!box) return;
    box.value.split(",").map((x) => x.trim()).filter(Boolean).forEach((x) => {
      if (!draft.interests.some((y) => y.toLowerCase() === x.toLowerCase())) draft.interests.push(x);
    });
    box.value = "";
  }
  if (k === "interests") {
    const count = () => ($("#pickedCount").textContent = draft.interests.length ? `You've picked ${draft.interests.length} interest${draft.interests.length > 1 ? "s" : ""}.` : "Pick or add at least one, or press Next to skip.");
    count();
    $$("[data-int]", main).forEach((b) => b.addEventListener("click", () => {
      const x = b.dataset.int, on = draft.interests.includes(x);
      draft.interests = on ? draft.interests.filter((y) => y !== x) : [...draft.interests, x]; b.setAttribute("aria-pressed", !on); count();
    }));
    const addAndDraw = () => { if (!$("#f-own").value.trim()) { $("#f-own").focus(); return toast("Type an interest first."); } addOwn(); render(); setTimeout(() => $("#f-own")?.focus(), 30); };
    $("#ownAdd").addEventListener("click", addAndDraw);
    $("#f-own").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addAndDraw(); } });
    $$("[data-rmint]", main).forEach((b) => b.addEventListener("click", () => { draft.interests = draft.interests.filter((y) => y !== b.dataset.rmint); render(); }));
  }
  if (k === "meds") {
    let times = [], food = false;
    const drawTimes = () => ($("#m-times").innerHTML = times.length ? `<span class="hint" style="align-self:center">Times added:</span>` + times.map((t, i) => `<button class="chip" aria-pressed="true" data-rmt="${i}" aria-label="Remove ${fmtTime(t)}">${fmtTime(t)} ✕</button>`).join("") : "");
    $$("[data-qt]", main).forEach((b) => b.addEventListener("click", () => { setTime("m-time", b.dataset.qt); if (/breakfast|lunch|dinner/i.test(b.textContent) && !food) $("#m-food").click(); }));
    $("#m-addtime").addEventListener("click", () => { const t = getTime("m-time"); if (t && !times.includes(t)) { times.push(t); times.sort(); } drawTimes(); toast(`${fmtTime(t)} added. Pick the next time with the arrows.`); });
    $("#m-times").addEventListener("click", (e) => { const b = e.target.closest("[data-rmt]"); if (b) { times.splice(+b.dataset.rmt, 1); drawTimes(); } });
    $("#m-food").addEventListener("click", (e) => { food = !food; e.currentTarget.setAttribute("aria-pressed", food); });
    $("#m-add").addEventListener("click", () => {
      const name = $("#m-name").value.trim(); if (!name) return toast("Type the medicine's name.");
      const t = getTime("m-time"); if (t && !times.includes(t)) times.push(t);
      draft.medications.push({ name, dose: $("#m-dose").value.trim(), times: times.sort(), with_food: food }); render();
    });
    $$("[data-delmed]", main).forEach((b) => b.addEventListener("click", () => { draft.medications.splice(+b.dataset.delmed, 1); render(); }));
  }
  if (k === "acts") {
    const days = new Set();
    $$("[data-idea]", main).forEach((b) => b.addEventListener("click", () => ($("#a-name").value = b.dataset.idea)));
    const paint = () => $$("[data-day]", main).forEach((b) => b.setAttribute("aria-pressed", days.has(+b.dataset.day)));
    $$("[data-day]", main).forEach((b) => b.addEventListener("click", () => { const d = +b.dataset.day; days.has(d) ? days.delete(d) : days.add(d); paint(); }));
    $("#everyday").addEventListener("click", () => { for (let i = 0; i < 7; i++) days.add(i); paint(); });
    $("#a-add").addEventListener("click", () => {
      const name = $("#a-name").value.trim(); if (!name) return toast("Type what the activity is.");
      if (!days.size) return toast("Pick at least one day.");
      draft.activities.push({ name, days: [...days].sort(), time: getTime("a-time") }); render();
    });
    $$("[data-delact]", main).forEach((b) => b.addEventListener("click", () => { draft.activities.splice(+b.dataset.delact, 1); render(); }));
  }
}
async function findTown(auto) {
  const q = $("#f-town").value.trim(); if (!q) return;
  const box = $("#townResults"); box.innerHTML = `<div class="row"><span class="spinner"></span> Looking...</div>`;
  try {
    const { results } = await api("/api/geocode?q=" + encodeURIComponent(q));
    if (!results.length) { box.innerHTML = `<p>I couldn't find that town. Check the spelling, or skip for now.</p>`; return; }
    if (auto) { Object.assign(draft, { lat: results[0].lat, lon: results[0].lon, country: results[0].country }); api(`/api/prefetch?lat=${results[0].lat}&lon=${results[0].lon}`).catch(() => {}); return; }
    box.innerHTML = results.map((r, i) => `<button class="choice" data-town="${i}" aria-pressed="false"><span class="dot"></span>${esc(r.name)}, ${esc(r.region)} ${r.country !== "US" ? esc(r.country) : ""}</button>`).join("");
    $$("[data-town]", box).forEach((b) => b.addEventListener("click", () => {
      const r = results[+b.dataset.town];
      Object.assign(draft, { town: `${r.name}, ${r.region}`, lat: r.lat, lon: r.lon, country: r.country }); $("#f-town").value = draft.town;
      api(`/api/prefetch?lat=${r.lat}&lon=${r.lon}`).catch(() => {});                 // start downloading the map of this town now
      $$("[data-town]", box).forEach((x) => x.setAttribute("aria-pressed", x === b));
    }));
  } catch (e) { box.innerHTML = `<p>Couldn't look up towns right now (${esc(e.message)}). You can skip this and add it later.</p>`; }
}

// ------------------------------------------------------------------ start
async function boot() {
  try { S = await api("/api/state"); } catch (e) { main.innerHTML = `<div class="card"><h1>Lantern Guide isn't running</h1><p>${esc(e.message)}</p></div>`; return; }
  screen = S.profile ? "home" : "setup"; draft = null; step = 0; navStack = [];
  render();
}
tickClock(); setInterval(tickClock, 15000);
setInterval(reminderTick, 20000);
boot().then(() => setTimeout(reminderTick, 1500));
