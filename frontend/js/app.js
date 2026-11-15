/* MediQueue AI - router, layouts and public pages. */
const PRIVACY = "This academic demonstration system uses synthetic data and is not intended for storing real patient medical records.";
const DISCLAIMER = "Estimated waiting time. Actual time may vary depending on consultation duration and queue changes.";

const NAV = {
  PATIENT: [
    ["Patient", [["/patient", "▦", "Dashboard"], ["/patient/book", "＋", "Book Appointment"], ["/patient/queue", "◷", "My Queue"],
      ["/patient/appointments", "☰", "Appointments"], ["/patient/departments", "▤", "Departments"], ["/patient/doctors", "⚕", "Doctors"], ["/patient/profile", "◉", "Profile"]]],
  ],
  STAFF: [
    ["Operations", [["/staff", "▦", "Dashboard"], ["/staff/appointments", "☰", "Today's Appointments"], ["/staff/queue", "◷", "Active Queue"],
      ["/staff/manage", "⇅", "Queue Management"], ["/staff/predictions", "◎", "Predictions"], ["/staff/analytics", "▥", "Analytics"], ["/staff/profile", "◉", "Profile"]]],
  ],
  ADMIN: [
    ["Overview", [["/admin", "▦", "Dashboard"], ["/admin/analytics", "▥", "Analytics"]]],
    ["Hospital", [["/admin/departments", "▤", "Departments"], ["/admin/doctors", "⚕", "Doctors"], ["/admin/staff", "☺", "Staff"], ["/admin/settings", "⚙", "Settings"]]],
    ["Operations", [["/admin/appointments", "☰", "Appointments"], ["/admin/queues", "◷", "Queues"]]],
    ["AI / ML", [["/admin/ml", "◎", "ML Model"], ["/admin/training-data", "⊞", "Training Data"], ["/admin/history", "⧗", "Queue History"]]],
  ],
};
const HOME = { PATIENT: "/patient", STAFF: "/staff", ADMIN: "/admin" };

const App = {
  user: null, timers: [], routes: {},
  route(path, role, render) { this.routes[path] = { role, render }; },
  go(path) { if (location.hash !== "#" + path) location.hash = "#" + path; else this.render(); },
  every(ms, fn) { this.timers.push(setInterval(fn, ms)); },
  clearTimers() { this.timers.forEach(clearInterval); this.timers = []; },
  async init() {
    try { this.user = (await API.get("/api/auth/me")).user; } catch (e) { this.user = null; }
    window.addEventListener("hashchange", () => this.render());
    this.render();
  },
  async render() {
    this.clearTimers();
    const path = (location.hash.slice(1) || "/").split("?")[0];
    const r = this.routes[path];
    const root = $("#root");
    if (!r) { root.innerHTML = this.publicShell(`<div class="wrap section">${empty("Page not found.")}</div>`); return; }
    if (r.role && !this.user) return this.go("/login");
    if (r.role && r.role !== this.user.role && !(r.role === "STAFF" && this.user.role === "ADMIN")) return this.go(HOME[this.user.role]);
    if (r.role) {
      root.innerHTML = this.appShell(path);
      const main = $("#main");
      main.innerHTML = loading("Loading…");
      try { await r.render(main); }
      catch (e) { main.innerHTML = errorBox(e.message || "Something went wrong."); }
    } else {
      root.innerHTML = this.publicShell("<div id='pub'></div>");
      await r.render($("#pub"));
      $$(".nav a").forEach((a) => a.classList.toggle("active", a.getAttribute("href") === "#" + path));
    }
    window.scrollTo(0, 0);
  },
  brand() { return `<a class="brand" href="#/"><span class="logo">MQ</span><span>MediQueue <span class="ai">AI</span></span></a>`; },
  publicShell(inner) {
    const authed = this.user ? `<a class="btn sm" href="#${HOME[this.user.role]}">Open dashboard</a>` :
      `<a href="#/login">Log in</a><a class="btn sm" href="#/register" style="color:#fff">Register</a>`;
    return `<header class="topbar"><div class="wrap">${this.brand()}
      <button class="menu-btn" onclick="$('.nav').classList.toggle('open')" aria-label="Menu">☰</button>
      <nav class="nav"><a href="#/about">About</a><a href="#/how-it-works">How It Works</a><a href="#/features">Features</a><a href="#/help">Help</a>${authed}</nav></div></header>
      ${inner}
      <footer class="footer"><div class="wrap"><strong>MediQueue AI</strong> · Smart Queue Management. Predictable Waiting.<br>${PRIVACY}</div></footer>`;
  },
  appShell(path) {
    const u = this.user;
    const groups = NAV[u.role].map(([title, links]) => `<div class="side-section">${title}</div>` + links.map(([href, ic, label]) =>
      `<a class="side-link ${href === path ? "active" : ""}" href="#${href}"><span class="ic">${ic}</span>${label}</a>`).join("")).join("");
    const extra = u.role === "ADMIN" ? `<div class="side-section">Staff view</div><a class="side-link ${path === "/staff/queue" ? "active" : ""}" href="#/staff/queue"><span class="ic">◷</span>Live Queue Console</a>` : "";
    return `<div class="mobile-bar"><button class="btn secondary sm" onclick="$('.sidebar').classList.toggle('open')" aria-label="Menu">☰</button>${this.brand()}</div>
      <div class="app"><aside class="sidebar">${this.brand()}${groups}${extra}
        <div class="side-user"><strong>${esc(u.full_name)}</strong><br><small>${esc(u.email)}</small><br>
          <span class="badge" style="margin-top:6px">${u.role}</span>
          <div style="margin-top:10px"><button class="btn secondary sm" id="logout">Log out</button></div></div>
      </aside><main class="main" id="main"></main></div>`;
  },
};
document.addEventListener("click", async (e) => {
  if (e.target.id === "logout") {
    await API.post("/api/auth/logout"); App.user = null; App.go("/login");
  }
  if (e.target.closest(".side-link")) $(".sidebar")?.classList.remove("open");
});

/* ============================ PUBLIC PAGES ============================ */
App.route("/", null, async (el) => {
  el.innerHTML = `<section class="hero"><div class="wrap hero-grid"><div>
      <span class="badge ai">B.Tech AIML Project · Random Forest regression</span>
      <h1 style="margin-top:14px">Smart Queue Management.<br>Predictable Waiting.</h1>
      <p class="lead">MediQueue AI books outpatient appointments, issues digital queue tokens, tracks the live queue and
      predicts each patient's waiting time with a machine-learning model trained on the hospital's own queue history.</p>
      <div class="btn-row" style="margin-top:20px"><a class="btn lg" href="#/register" style="color:#fff">Book an appointment</a>
      <a class="btn secondary lg" href="#/how-it-works">How it works</a></div></div>
      <div class="card" id="live-card">${loading("Loading live hospital data…")}</div></div></section>
    <section class="section"><div class="wrap"><h2>One system for patients, staff and administrators</h2>
      <div class="grid g3" style="margin-top:16px">
        <div class="card feat"><div class="ic">◷</div><h3>Patients</h3><p>Book a slot, receive a queue number such as GM-014, follow your position live and see an ML-estimated waiting time.</p></div>
        <div class="card feat"><div class="ic">⇅</div><h3>Staff</h3><p>Call the next patient, start and complete consultations, mark no-shows. Every action updates the queue and records real waiting times.</p></div>
        <div class="card feat"><div class="ic">◎</div><h3>Administrators</h3><p>Validate historical data, train and evaluate new model versions (MAE, RMSE, R²), compare against a baseline and activate the best one.</p></div>
      </div></div></section>`;
  try {
    const [deps, docs] = await Promise.all([API.get("/api/departments"), API.get("/api/doctors")]);
    $("#live-card").innerHTML = `<h3>Hospital at a glance</h3><p class="muted">Live from the database</p>
      <div class="grid g2"><div class="stat"><div class="label">Departments</div><div class="value">${deps.departments.length}</div></div>
      <div class="stat"><div class="label">Doctors</div><div class="value">${docs.doctors.length}</div></div></div>
      <div style="margin-top:8px">${deps.departments.map((d) => `<span class="badge" style="margin:3px">${esc(d.prefix)} · ${esc(d.name)}</span>`).join("")}</div>`;
  } catch (e) { $("#live-card").innerHTML = errorBox("Hospital information is temporarily unavailable."); }
});

App.route("/about", null, async (el) => {
  el.innerHTML = `<div class="wrap section" style="max-width:860px"><h1>About MediQueue AI</h1>
    <p><strong>AI-Based Smart Hospital Queue Management and Waiting-Time Prediction System</strong> — a B.Tech Artificial Intelligence & Machine Learning mini project.</p>
    <div class="card"><h3>The problem</h3><p>Outpatient departments often have long, unpredictable waits and no visibility into queue progress. Patients cannot plan; staff manage queues on paper.</p>
    <h3>The approach</h3><p>Every completed consultation becomes a training record (queue position at arrival, queue length, doctor, department, hour, day, recent waits, arrival delay → actual wait).
    A Random Forest Regressor learns the relationship and predicts how long a waiting patient will wait. Predictions are logged and later compared with the real wait so model quality is continuously measured.</p>
    <h3>Scope</h3><p>Appointments, queue management, waiting-time prediction and analytics only. The system does not store diagnoses, prescriptions or any medical records.</p></div>
    <div class="notice">${PRIVACY}</div></div>`;
});

App.route("/how-it-works", null, async (el) => {
  el.innerHTML = `<div class="wrap section" style="max-width:960px"><h1>How it works</h1>
    <div class="card"><h3>Patient journey</h3><div class="flow"><span>Department</span><i>→</i><span>Doctor</span><i>→</i><span>Date</span><i>→</i><span>Slot</span><i>→</i><span>Queue number</span><i>→</i><span>Live queue + AI estimate</span></div></div>
    <div class="card"><h3>Queue states</h3><div class="flow">${fmt.status("WAITING")}<i>→</i>${fmt.status("CALLED")}<i>→</i>${fmt.status("SERVING")}<i>→</i>${fmt.status("COMPLETED")}</div>
      <p style="margin-top:10px">A waiting or called patient can also become ${fmt.status("NO_SHOW")} or ${fmt.status("CANCELLED")}. Invalid transitions are rejected by the server.</p></div>
    <div class="card"><h3>Machine-learning pipeline</h3><div class="flow"><span>QueueHistory</span><i>→</i><span>Validation</span><i>→</i><span>Feature engineering</span><i>→</i><span>Chronological split</span><i>→</i><span>Random Forest</span><i>→</i><span>MAE · RMSE · R²</span><i>→</i><span>Model version</span><i>→</i><span>Activate</span><i>→</i><span>Predictions</span></div>
      <p style="margin-top:12px">Waiting time is measured from check-in (joining the queue) to the start of consultation. The model never sees the answer it is predicting: features only use information available before the patient is served.</p></div>
    <div class="notice">${PRIVACY}</div></div>`;
});

App.route("/features", null, async (el) => {
  const f = [["Appointment booking", "Department → doctor → date → slot, with double-booking, past-date and schedule checks on the server."],
    ["Digital queue tokens", "Department-prefixed numbers (GM-001, CAR-004) unique per department and day, enforced by the database."],
    ["Live queue tracking", "Position, patients ahead and now-serving token refresh automatically every few seconds."],
    ["ML waiting-time prediction", "Random Forest Regressor served from the active model version; each prediction is stored with its inputs."],
    ["Model training & versioning", "Admins train WaitingTimeModel v1, v2, … and activate exactly one. Failed runs never replace the active model."],
    ["Evaluation & monitoring", "Chronological test-set MAE, RMSE, R², time-series cross-validation, baseline comparison, and live predicted-vs-actual error."],
    ["Data quality checks", "Missing, invalid and duplicate history records are detected and excluded before training."],
    ["Operational analytics", "Waits by department and hour, patients served, no-show and cancellation rates — computed from the database."],
    ["Role-based security", "Patient, staff and admin permissions enforced on every API call, not just hidden in the UI."]];
  el.innerHTML = `<div class="wrap section"><h1>Features</h1><div class="grid g3" style="margin-top:12px">${f.map(([t, d]) => `<div class="card"><h3>${t}</h3><p>${d}</p></div>`).join("")}</div></div>`;
});

App.route("/help", null, async (el) => {
  el.innerHTML = `<div class="wrap section" style="max-width:860px"><h1>Help</h1>
    <div class="card"><h3>Demo accounts</h3><p>All demo accounts use the password <code>Demo@1234</code>.</p>
    <div class="table-wrap"><table class="demo-accts"><tr><th>Role</th><th>Email</th><th>Notes</th></tr>
      <tr><td>Patient</td><td>patient@mediqueue.demo</td><td>No appointment today — book one to join a live queue</td></tr>
      <tr><td>Patient</td><td>patient2@mediqueue.demo</td><td>Second patient for isolation tests</td></tr>
      <tr><td>Staff</td><td>staff@mediqueue.demo</td><td>All departments</td></tr>
      <tr><td>Staff</td><td>gm.staff@mediqueue.demo</td><td>General Medicine only</td></tr>
      <tr><td>Admin</td><td>admin@mediqueue.demo</td><td>Full access incl. ML model</td></tr></table></div></div>
    <div class="card"><h3>Why does my estimate change?</h3><p>The estimate is recalculated when the queue changes (someone is called, cancels or doesn't show) or every few minutes. ${DISCLAIMER}</p>
    <h3>When does the prediction appear?</h3><p>On the day of your appointment, once you have joined the queue (same-day bookings join automatically; otherwise tap “Check in” when you arrive).</p></div>
    <div class="notice">${PRIVACY}</div></div>`;
});

function authForm(el, mode) {
  const reg = mode === "register";
  el.innerHTML = `<div class="auth-wrap"><div class="card"><h1>${reg ? "Create your account" : "Welcome back"}</h1>
    <p>${reg ? "Register as a patient to book appointments." : "Log in to MediQueue AI."}</p><div id="msg"></div>
    <form id="f" novalidate>
      ${reg ? `<div class="field"><label for="n">Full name</label><input id="n" required autocomplete="name"></div>
      <div class="field"><label for="ph">Phone (optional)</label><input id="ph" autocomplete="tel"></div>` : ""}
      <div class="field"><label for="e">Email</label><input id="e" type="email" required autocomplete="email"></div>
      <div class="field"><label for="p">Password</label><input id="p" type="password" required autocomplete="${reg ? "new-password" : "current-password"}">
      ${reg ? `<div class="hint">At least 8 characters, with letters and numbers.</div>` : ""}</div>
      <button class="btn lg" style="width:100%" id="go">${reg ? "Create account" : "Log in"}</button></form>
    <p style="margin-top:14px;text-align:center">${reg ? `Already registered? <a href="#/login">Log in</a>` : `New patient? <a href="#/register">Create an account</a>`}</p>
    ${reg ? "" : `<div class="notice">Demo accounts (password <code>Demo@1234</code>): patient@mediqueue.demo · staff@mediqueue.demo · admin@mediqueue.demo</div>`}
  </div></div>`;
  $("#f").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const email = $("#e").value.trim(), password = $("#p").value;
    if (!email || !password || (reg && !$("#n").value.trim())) { $("#msg").innerHTML = errorBox("Please fill in all required fields."); return; }
    await busy($("#go"), reg ? "Creating account…" : "Signing in…", async () => {
      try {
        const body = reg ? { full_name: $("#n").value.trim(), phone: $("#ph").value.trim(), email, password } : { email, password };
        const r = await API.post(reg ? "/api/auth/register" : "/api/auth/login", body);
        App.user = r.user; App.go(HOME[r.user.role]);
      } catch (e) { $("#msg").innerHTML = errorBox(e.message); }
    });
  });
}
App.route("/login", null, async (el) => { if (App.user) return App.go(HOME[App.user.role]); authForm(el, "login"); });
App.route("/register", null, async (el) => { if (App.user) return App.go(HOME[App.user.role]); authForm(el, "register"); });

/* shared profile page */
async function profilePage(el) {
  const u = App.user;
  el.innerHTML = `<div class="page-head"><div><h1>Profile</h1><p>Your account details.</p></div></div>
    <div class="grid g2"><div class="card"><h3>Details</h3><div id="pm"></div>
      <div class="field"><label>Full name</label><input id="pn" value="${esc(u.full_name)}"></div>
      <div class="field"><label>Email</label><input value="${esc(u.email)}" disabled></div>
      <div class="field"><label>Phone</label><input id="pp" value="${esc(u.phone || "")}"></div>
      <div class="field"><label>Role</label><input value="${u.role}" disabled></div>
      <button class="btn" id="ps">Save changes</button></div>
    <div class="card"><h3>Change password</h3><div id="pwm"></div>
      <div class="field"><label>Current password</label><input id="c1" type="password"></div>
      <div class="field"><label>New password</label><input id="c2" type="password"><div class="hint">At least 8 characters, letters and numbers.</div></div>
      <button class="btn secondary" id="pws">Update password</button></div></div>
    <div class="notice">${PRIVACY}</div>`;
  $("#ps").onclick = (e) => busy(e.currentTarget, "Saving…", async () => {
    try { App.user = (await API.put("/api/auth/profile", { full_name: $("#pn").value, phone: $("#pp").value })).user; $("#pm").innerHTML = `<div class="alert success">Profile updated.</div>`; }
    catch (err) { $("#pm").innerHTML = errorBox(err.message); }
  });
  $("#pws").onclick = (e) => busy(e.currentTarget, "Updating…", async () => {
    try { await API.put("/api/auth/password", { current_password: $("#c1").value, new_password: $("#c2").value }); $("#pwm").innerHTML = `<div class="alert success">Password updated.</div>`; $("#c1").value = $("#c2").value = ""; }
    catch (err) { $("#pwm").innerHTML = errorBox(err.message); }
  });
}
