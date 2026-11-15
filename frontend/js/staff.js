/* ============================ STAFF PAGES ============================ */
const scopeNote = () => App.user.role === "STAFF" && App.user.department_id ? `<span class="badge">Department-scoped account</span>` : "";

App.route("/staff", "STAFF", async (el) => {
  const draw = async () => {
    const d = await API.get("/api/staff/dashboard");
    el.innerHTML = `<div class="page-head"><div><h1>Operations Dashboard</h1><p>${fmt.date(d.date)} · live from the queue engine ${scopeNote()}</p></div>
        <a class="btn" style="color:#fff" href="#/staff/queue">Open queue console</a></div>
      <div class="grid g3">${[["Today's appointments", d.appointments], ["Patients waiting", d.waiting], ["Currently serving", d.serving],
        ["Completed", d.completed], ["No-shows", d.no_shows], ["Cancellations", d.cancelled]].map(([l, v]) => `<div class="card stat"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>
      <div class="card" style="margin-top:16px"><div class="card-head"><h3>Active queues</h3><small class="muted">Average measured wait today: ${fmt.min(d.avg_wait_today)}</small></div>
      ${d.queues.length ? `<div class="grid g3">${d.queues.map((q) => `<a class="card qcard" style="text-decoration:none;color:inherit" href="#/staff/queue?doctor=${q.doctor_id}">
          <div class="card-head"><strong>${esc(q.department_name).toUpperCase()}</strong><span class="badge">${esc(q.prefix)}</span></div>
          <div>${esc(q.doctor_name)}</div><div style="margin-top:8px;font-size:.9rem">Now serving: <strong>${esc(q.now_serving || "–")}</strong></div>
          <div style="margin-top:6px"><span class="badge WAITING">${q.waiting} waiting</span> <span class="badge COMPLETED">${q.completed} done</span></div></a>`).join("")}</div>` : empty("No queues today.")}</div>`;
  };
  await draw();
  App.every(8000, () => draw().catch(() => {}));
});

App.route("/staff/appointments", "STAFF", async (el) => {
  const r = await API.get("/api/staff/appointments");
  el.innerHTML = `<div class="page-head"><div><h1>Today's Appointments</h1><p>${fmt.date(r.date)} · ${r.appointments.length} appointments ${scopeNote()}</p></div>
      <input id="flt" placeholder="Filter by name, doctor or queue no." style="max-width:300px"></div>
    <div class="card"><div class="table-wrap"><table id="t"><tr><th>Time</th><th>Queue no.</th><th>Patient</th><th>Doctor</th><th>Department</th><th>Appointment</th><th>Queue</th><th>Checked in</th></tr>
    ${r.appointments.map((a) => `<tr><td>${esc(a.appointment_time)}</td><td><strong>${esc(a.queue_number)}</strong></td><td>${esc(a.patient_name)}</td><td>${esc(a.doctor_name)}</td><td>${esc(a.department_name)}</td>
      <td>${fmt.status(a.status)}</td><td>${a.queue_status ? fmt.status(a.queue_status) : "–"}</td><td>${a.joined_at ? fmt.time(a.joined_at) : "–"}</td></tr>`).join("")}</table></div>
    ${r.appointments.length ? "" : empty("No appointments today.")}</div>`;
  $("#flt").oninput = () => { const v = $("#flt").value.toLowerCase(); $$("#t tr").slice(1).forEach((tr) => tr.style.display = tr.textContent.toLowerCase().includes(v) ? "" : "none"); };
});

App.route("/staff/queue", "STAFF", async (el) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const qs = (await API.get("/api/staff/queues")).queues;
  let doctor = params.get("doctor") || (qs[0] && qs[0].doctor_id);
  el.innerHTML = `<div class="page-head"><div><h1>Active Queue</h1><p>Call, start, complete and close consultations. Every action is recorded.</p></div>
      <select id="ds" style="max-width:340px">${qs.map((q) => `<option value="${q.doctor_id}" ${q.doctor_id == doctor ? "selected" : ""}>${esc(q.department_name)} · ${esc(q.doctor_name)}</option>`).join("")}</select></div>
    <div id="msg"></div><div id="qc">${qs.length ? loading("Loading queue…") : empty("No queues today.")}</div>`;
  if (!qs.length) return;
  $("#ds").onchange = () => { doctor = $("#ds").value; history.replaceState(null, "", `#/staff/queue?doctor=${doctor}`); draw(); };
  let acting = false;
  async function act(btn, url, label) {
    acting = true;
    await busy(btn, label, async () => {
      try { await API.post(url); $("#msg").innerHTML = ""; await draw(); }
      catch (e) { $("#msg").innerHTML = errorBox(e.message); }
    });
    acting = false;
  }
  async function draw() {
    let r;
    try { r = await API.get(`/api/staff/queues/${doctor}`); }
    catch (e) { $("#qc").innerHTML = errorBox(e.message); return; }
    const active = r.entries.filter((e) => ["SERVING", "CALLED", "WAITING"].includes(e.status));
    const closed = r.entries.filter((e) => !["SERVING", "CALLED", "WAITING"].includes(e.status));
    const btns = (e) => {
      const b = [];
      if (e.status === "WAITING" && !e.joined_at) b.push(`<button class="btn secondary sm" data-a="check-in" data-id="${e.id}">Mark arrived</button>`);
      if (e.status === "WAITING" && e.joined_at) b.push(`<button class="btn secondary sm" data-a="call" data-id="${e.id}">Call</button>`);
      if (e.status === "CALLED") b.push(`<button class="btn sm" data-a="start" data-id="${e.id}">Start service</button>`);
      if (e.status === "SERVING") b.push(`<button class="btn green sm" data-a="complete" data-id="${e.id}">Complete</button>`);
      if (["WAITING", "CALLED"].includes(e.status)) b.push(`<button class="btn warn sm" data-a="no-show" data-id="${e.id}">No show</button><button class="btn danger sm" data-a="cancel" data-id="${e.id}">Cancel</button>`);
      return b.join("");
    };
    $("#qc").innerHTML = `<div class="card"><div class="card-head"><div><h3 style="margin:0">${esc(r.doctor.department_name).toUpperCase()}</h3><small class="muted">${esc(r.doctor.name)} · ${fmt.date(r.date)}</small></div>
        <button class="btn teal lg" id="next">▶ CALL NEXT</button></div>
        ${active.length ? active.map((e) => `<div class="qrow ${e.status}"><div class="tokn">${esc(e.queue_number)}</div>
          <div class="who"><strong>${esc(e.patient_name)}</strong><small class="muted">Slot ${esc(e.appointment_time)} · ${e.joined_at ? "checked in " + fmt.time(e.joined_at) : "not checked in"}${e.predicted_waiting_time != null && e.status === "WAITING" ? ` · AI est. ${Math.round(e.predicted_waiting_time)} min` : ""}${e.actual_waiting_time != null ? ` · waited ${fmt.n(e.actual_waiting_time)} min` : ""}</small></div>
          ${fmt.status(e.status)}<div class="btn-row">${btns(e)}</div></div>`).join("") : empty("No active patients in this queue.")}</div>
      <div class="card"><h3>Closed today (${closed.length})</h3>${closed.length ? `<div class="table-wrap"><table><tr><th>Queue no.</th><th>Patient</th><th>Status</th><th class="num">Wait (min)</th><th class="num">Consult (min)</th><th class="num">AI predicted</th></tr>
        ${closed.map((e) => `<tr><td><strong>${esc(e.queue_number)}</strong></td><td>${esc(e.patient_name)}</td><td>${fmt.status(e.status)}</td><td class="num">${fmt.n(e.actual_waiting_time)}</td><td class="num">${fmt.n(e.consultation_duration)}</td><td class="num">${fmt.n(e.predicted_waiting_time)}</td></tr>`).join("")}</table></div>` : empty("Nothing closed yet.")}</div>`;
    $("#next").onclick = (ev) => act(ev.currentTarget, `/api/staff/queues/${doctor}/call-next`, "Calling…");
    $$("[data-a]").forEach((b) => b.onclick = () => act(b, `/api/staff/entries/${b.dataset.id}/${b.dataset.a}`, "…"));
  }
  await draw();
  App.every(6000, () => { if (!acting) draw(); });
});

App.route("/staff/manage", "STAFF", async (el) => {
  const r = await API.get("/api/staff/queues");
  el.innerHTML = `<div class="page-head"><div><h1>Queue Management</h1><p>All of today's doctor queues ${scopeNote()}</p></div></div>
    <div class="card">${r.queues.length ? `<div class="table-wrap"><table><tr><th>Department</th><th>Doctor</th><th>Now serving</th><th class="num">Waiting</th><th class="num">Called</th><th class="num">Completed</th><th class="num">Total</th><th></th></tr>
    ${r.queues.map((q) => `<tr><td>${esc(q.department_name)}</td><td>${esc(q.doctor_name)}</td><td><strong>${esc(q.now_serving || "–")}</strong></td><td class="num">${q.waiting}</td><td class="num">${q.called}</td><td class="num">${q.completed}</td><td class="num">${q.total}</td>
      <td class="num"><a class="btn sm" style="color:#fff" href="#/staff/queue?doctor=${q.doctor_id}">Manage</a></td></tr>`).join("")}</table></div>` : empty("No queues today.")}</div>`;
});

async function predictionsPage(el, url) {
  const r = await API.get(url);
  const s = r.summary;
  el.innerHTML = `<div class="page-head"><div><h1>Predictions</h1><p>Latest ML prediction per appointment, compared with the actual wait once the consultation starts.</p></div></div>
    <div class="grid g3"><div class="card stat"><div class="label">Evaluated predictions</div><div class="value">${s.n}</div></div>
      <div class="card stat"><div class="label">Live MAE (ML model)</div><div class="value">${fmt.min(s.live_mae)}</div><div class="sub">mean |predicted − actual|</div></div>
      <div class="card stat"><div class="label">Live MAE (baseline)</div><div class="value">${fmt.min(s.baseline_live_mae)}</div><div class="sub">patients ahead × avg consult</div></div></div>
    <div class="card" style="margin-top:16px"><div class="table-wrap"><table><tr><th>Queue no.</th><th>Doctor</th><th>Predicted at</th><th>Model</th><th class="num">Predicted</th><th class="num">Baseline</th><th class="num">Actual</th><th class="num">Error</th></tr>
    ${r.recent.map((p) => `<tr><td><strong>${esc(p.queue_number)}</strong></td><td>${esc(p.doctor_name)}<br><small class="muted">${esc(p.department_name)}</small></td><td>${fmt.dt(p.prediction_timestamp)}</td><td>v${p.model_version}</td>
      <td class="num">${fmt.n(p.predicted_waiting_time)} min</td><td class="num">${fmt.n(p.baseline_waiting_time)} min</td><td class="num">${p.actual_waiting_time != null ? fmt.n(p.actual_waiting_time) + " min" : `<span class="badge WAITING">pending</span>`}</td>
      <td class="num">${p.absolute_error != null ? `<strong>${fmt.n(p.absolute_error)}</strong> min` : "–"}</td></tr>`).join("")}</table></div>
    ${r.recent.length ? "" : empty("No predictions yet.")}</div>`;
}
App.route("/staff/predictions", "STAFF", (el) => predictionsPage(el, "/api/staff/predictions"));

async function analyticsPage(el, url) {
  const draw = async (days) => {
    const a = await API.get(`${url}?days=${days}`);
    const t = a.totals;
    const pva = a.predicted_vs_actual.map((p) => ({ x: p.actual, y: p.predicted }));
    el.innerHTML = `<div class="page-head"><div><h1>Analytics</h1><p>Computed from the database · last ${a.period_days} days ${scopeNote()}</p></div>
        <select id="days" style="max-width:160px">${[7, 14, 30, 60].map((d) => `<option ${d == days ? "selected" : ""} value="${d}">Last ${d} days</option>`).join("")}</select></div>
      <div class="grid g4">${[["Appointments", t.appointments], ["Completed", t.completed], ["No-show rate", fmt.pct(t.no_show_rate)], ["Cancellation rate", fmt.pct(t.cancellation_rate)]]
        .map(([l, v]) => `<div class="card stat"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>
      <div class="grid g2" style="margin-top:16px">
        <div class="card"><h3>Average waiting time by department</h3>${Charts.hbars(a.avg_wait_by_department, { unit: " min" })}</div>
        <div class="card"><h3>Average waiting time by hour</h3>${Charts.columns(a.avg_wait_by_hour.map((r) => ({ label: r.label + ":00", value: r.value })), { yLabel: "min" })}</div>
        <div class="card"><h3>Patients served per day</h3>${Charts.line(a.patients_served_per_day.map((r) => ({ label: r.label.slice(5), value: r.value })), { labelEvery: Math.ceil(a.patients_served_per_day.length / 8) || 1 })}</div>
        <div class="card"><h3>Average queue length by hour</h3>${Charts.columns(a.queue_length_by_hour.map((r) => ({ label: r.label + ":00", value: r.value })), { color: "#0f8b8d", yLabel: "patients" })}</div>
        <div class="card"><h3>Appointment status distribution</h3>${Charts.hbars(a.appointment_status_distribution.map((r) => ({ label: r.label.replace("_", " "), value: r.value })), { digits: 0, color: "#1d5fd1" })}</div>
        <div class="card"><h3>Predicted vs actual waiting time</h3><p class="hint">Live predictions scored against the real wait (${pva.length} records).</p>${Charts.scatter(pva, { height: 280 })}</div>
      </div>`;
    $("#days").onchange = () => draw($("#days").value);
  };
  await draw(30);
}
App.route("/staff/analytics", "STAFF", (el) => analyticsPage(el, "/api/staff/analytics"));
App.route("/staff/profile", "STAFF", profilePage);
