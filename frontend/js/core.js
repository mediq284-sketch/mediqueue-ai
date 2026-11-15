/* MediQueue AI - core: API client, helpers, charts. No secrets live in the frontend;
   authentication is an HttpOnly session cookie set by the server. */
const API = {
  async req(method, url, body) {
    const opt = { method, headers: {}, credentials: "same-origin" };
    if (body !== undefined) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(url, opt); }
    catch (e) { throw new Error("Unable to reach the server. Please check your connection."); }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-JSON */ }
    if (!res.ok) {
      const err = new Error((data && data.error) || "Something went wrong. Please try again.");
      err.status = res.status; err.data = data;
      if (res.status === 401 && !url.startsWith("/api/auth/")) { App.user = null; App.go("/login"); }
      throw err;
    }
    return data;
  },
  get: (u) => API.req("GET", u),
  post: (u, b) => API.req("POST", u, b || {}),
  put: (u, b) => API.req("PUT", u, b || {}),
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const fmt = {
  n: (v, d = 1) => (v === null || v === undefined || isNaN(v)) ? "–" : Number(v).toFixed(d),
  min: (v) => (v === null || v === undefined) ? "–" : `${Math.round(v)} min`,
  date: (s) => { if (!s) return "–"; const d = new Date(s.length <= 10 ? s + "T00:00:00" : s.replace(" ", "T")); return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }); },
  dt: (s) => { if (!s) return "–"; const d = new Date(s.replace(" ", "T")); return d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); },
  time: (s) => { if (!s) return "–"; const d = new Date(s.replace(" ", "T")); return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" }); },
  pct: (v) => (v === null || v === undefined) ? "–" : `${Number(v).toFixed(1)}%`,
  day: (i) => ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][i],
  status: (s) => `<span class="badge ${esc(s)}"><span class="dot"></span>${esc(String(s).replace("_", " "))}</span>`,
};
const todayISO = () => { const d = new Date(); return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); };
const loading = (msg) => `<div class="loading"><div class="spinner"></div>${esc(msg)}</div>`;
const errorBox = (msg) => `<div class="alert error">${esc(msg)}</div>`;
const empty = (msg) => `<div class="empty">${esc(msg)}</div>`;

function toast(msg, type = "") {
  const host = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${type}`; el.textContent = msg; host.appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

/** Run an async action with the button disabled (prevents duplicate submissions). */
async function busy(btn, label, fn) {
  const old = btn.innerHTML; btn.disabled = true;
  btn.innerHTML = `<span class="spinner" style="width:14px;height:14px;border-width:2px"></span>${esc(label)}`;
  try { return await fn(); }
  finally { if (btn.isConnected) { btn.disabled = false; btn.innerHTML = old; } }
}

function modal(html) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal" role="dialog" aria-modal="true">${html}</div>`;
  back.addEventListener("click", (e) => { if (e.target === back) back.remove(); });
  document.body.appendChild(back);
  return { el: $(".modal", back), close: () => back.remove() };
}

/* ---------------- minimal SVG charts (no external libraries) ---------------- */
const Charts = {
  hbars(items, { unit = "", color = "var(--teal)", digits = 1 } = {}) {
    if (!items || !items.length) return empty("No data yet.");
    const max = Math.max(...items.map((i) => i.value || 0), 1);
    return items.map((i) => `<div class="bar-row"><div class="lbl" title="${esc(i.label)}">${esc(i.label)}</div>
      <div class="track"><div class="fill" style="width:${(100 * (i.value || 0) / max).toFixed(1)}%;background:${color}"></div></div>
      <div class="v">${fmt.n(i.value, digits)}${unit}</div></div>`).join("");
  },
  _axes(W, H, P, ymax, yTicks = 4) {
    let s = "";
    for (let t = 0; t <= yTicks; t++) {
      const v = ymax * t / yTicks, y = H - P.b - (H - P.t - P.b) * t / yTicks;
      s += `<line x1="${P.l}" x2="${W - P.r}" y1="${y}" y2="${y}" stroke="#eef2f6"/>
            <text x="${P.l - 6}" y="${y + 4}" text-anchor="end">${Math.round(v)}</text>`;
    }
    return s;
  },
  columns(items, { color = "#1d5fd1", height = 220, labelEvery = 1, yLabel = "" } = {}) {
    if (!items || !items.length) return empty("No data yet.");
    const W = 640, H = height, P = { l: 40, r: 10, t: 14, b: 30 };
    const ymax = Math.max(...items.map((i) => i.value || 0), 1) * 1.1;
    const bw = (W - P.l - P.r) / items.length;
    let s = this._axes(W, H, P, ymax);
    items.forEach((it, k) => {
      const h = (H - P.t - P.b) * (it.value || 0) / ymax, x = P.l + k * bw + bw * 0.15, y = H - P.b - h;
      s += `<rect x="${x}" y="${y}" width="${bw * 0.7}" height="${h}" rx="3" fill="${color}"><title>${esc(it.label)}: ${fmt.n(it.value)}</title></rect>`;
      if (k % labelEvery === 0) s += `<text x="${x + bw * 0.35}" y="${H - P.b + 16}" text-anchor="middle">${esc(it.label)}</text>`;
    });
    if (yLabel) s += `<text x="4" y="10">${esc(yLabel)}</text>`;
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">${s}</svg>`;
  },
  line(items, { color = "#0f8b8d", height = 220, labelEvery = 1, yLabel = "" } = {}) {
    if (!items || !items.length) return empty("No data yet.");
    const W = 640, H = height, P = { l: 40, r: 12, t: 14, b: 30 };
    const ymax = Math.max(...items.map((i) => i.value || 0), 1) * 1.15;
    const step = items.length > 1 ? (W - P.l - P.r) / (items.length - 1) : 0;
    let s = this._axes(W, H, P, ymax), pts = [];
    items.forEach((it, k) => {
      const x = P.l + k * step, y = H - P.b - (H - P.t - P.b) * (it.value || 0) / ymax;
      pts.push(`${x},${y}`);
      s += `<circle cx="${x}" cy="${y}" r="3" fill="${color}"><title>${esc(it.label)}: ${fmt.n(it.value)}</title></circle>`;
      if (k % labelEvery === 0) s += `<text x="${x}" y="${H - P.b + 16}" text-anchor="middle">${esc(it.label)}</text>`;
    });
    s = `<polyline points="${pts.join(" ")}" fill="none" stroke="${color}" stroke-width="2.2"/>` + s;
    if (yLabel) s += `<text x="4" y="10">${esc(yLabel)}</text>`;
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">${s}</svg>`;
  },
  scatter(points, { height = 300, xLabel = "Actual (min)", yLabel = "Predicted (min)" } = {}) {
    if (!points || !points.length) return empty("No evaluated predictions yet.");
    const W = 520, H = height, P = { l: 44, r: 12, t: 14, b: 36 };
    const m = Math.max(...points.map((p) => Math.max(p.x, p.y)), 1) * 1.05;
    const sx = (v) => P.l + (W - P.l - P.r) * v / m, sy = (v) => H - P.b - (H - P.t - P.b) * v / m;
    let s = this._axes(W, H, P, m);
    for (let t = 0; t <= 4; t++) s += `<text x="${sx(m * t / 4)}" y="${H - P.b + 16}" text-anchor="middle">${Math.round(m * t / 4)}</text>`;
    s += `<line x1="${sx(0)}" y1="${sy(0)}" x2="${sx(m)}" y2="${sy(m)}" stroke="#a35f00" stroke-dasharray="5 4" stroke-width="1.5"/>`;
    points.forEach((p) => { s += `<circle cx="${sx(p.x)}" cy="${sy(p.y)}" r="3.2" fill="#1d5fd1" fill-opacity=".45"/>`; });
    s += `<text x="${(W) / 2}" y="${H - 4}" text-anchor="middle" class="ttl">${esc(xLabel)}</text>
          <text x="12" y="${H / 2}" text-anchor="middle" class="ttl" transform="rotate(-90 12 ${H / 2})">${esc(yLabel)}</text>`;
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img">${s}</svg>
      <div class="legend"><span><i style="background:#1d5fd1"></i>Test-set prediction</span><span><i style="background:#a35f00"></i>Perfect prediction (y = x)</span></div>`;
  },
};
