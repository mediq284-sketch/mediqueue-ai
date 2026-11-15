/* ============================ ADMIN PAGES ============================ */
const DAYS = [0, 1, 2, 3, 4, 5, 6];

App.route("/admin", "ADMIN", async (el) => {
  const d = await API.get("/api/admin/dashboard");
  const m = d.active_model;
  el.innerHTML = `<div class="page-head"><div><h1>Admin Dashboard</h1><p>Hospital configuration, operations and the waiting-time model.</p></div>
      <a class="btn" style="color:#fff" href="#/admin/ml">Open ML Model</a></div>
    <div class="grid g4">${[["Departments", d.departments], ["Doctors", d.doctors], ["Staff", d.staff], ["Patients", d.patients],
      ["Appointments today", d.appointments_today], ["Waiting now", d.waiting_now], ["Serving now", d.serving_now], ["Completed today", d.completed_today]]
      .map(([l, v]) => `<div class="card stat"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>
    <div class="grid g2" style="margin-top:16px">
      <div class="card"><div class="card-head"><h3>Active model</h3>${m ? `<span class="badge ACTIVE">ACTIVE</span>` : `<span class="badge FAILED">none</span>`}</div>
        ${m ? `<dl class="kv"><dt>Model</dt><dd>${esc(m.model_name)} v${m.version}</dd><dt>Trained</dt><dd>${fmt.dt(m.training_date)}</dd>
          <dt>Test MAE</dt><dd>${fmt.n(m.mae, 2)} min</dd><dt>Test RMSE</dt><dd>${fmt.n(m.rmse, 2)} min</dd><dt>R²</dt><dd>${fmt.n(m.r2_score, 3)}</dd></dl>`
          : `<p>No model is active. Patients see “Insufficient historical data for ML prediction.”</p>`}
        <p class="hint" style="margin-top:10px">${d.history_records} history records (${d.live_history_records} recorded live) · ${d.model_versions} model version(s)</p></div>
      <div class="card"><div class="card-head"><h3>Recent predictions vs actual</h3><a href="#/admin/ml">Monitoring</a></div>
        ${d.monitoring.recent.length ? `<div class="table-wrap"><table><tr><th>Queue</th><th class="num">Predicted</th><th class="num">Actual</th><th class="num">Error</th></tr>
        ${d.monitoring.recent.map((p) => `<tr><td>${esc(p.queue_number)}</td><td class="num">${fmt.n(p.predicted_waiting_time)}</td><td class="num">${p.actual_waiting_time != null ? fmt.n(p.actual_waiting_time) : "pending"}</td><td class="num">${p.absolute_error != null ? fmt.n(p.absolute_error) : "–"}</td></tr>`).join("")}</table></div>` : empty("No predictions yet.")}</div></div>`;
});

/* -------- departments -------- */
App.route("/admin/departments", "ADMIN", async (el) => {
  const draw = async () => {
    const r = await API.get("/api/admin/departments");
    el.innerHTML = `<div class="page-head"><div><h1>Departments</h1><p>Queue-number prefixes are per department (e.g. GM-001).</p></div><button class="btn" id="add">＋ Add department</button></div>
      <div class="card"><div class="table-wrap"><table><tr><th>Name</th><th>Prefix</th><th>Description</th><th class="num">Doctors</th><th>Status</th><th></th></tr>
      ${r.departments.map((d) => `<tr><td><strong>${esc(d.name)}</strong></td><td><span class="badge">${esc(d.prefix)}</span></td><td>${esc(d.description || "")}</td><td class="num">${d.doctor_count}</td>
        <td>${d.active ? fmt.status("ACTIVE") : `<span class="badge NO_SHOW">INACTIVE</span>`}</td><td class="num"><button class="btn secondary sm" data-e="${d.id}">Edit</button></td></tr>`).join("")}</table></div></div>`;
    const open = (d = {}) => {
      const m = modal(`<h2>${d.id ? "Edit" : "Add"} department</h2><div id="mm"></div>
        <div class="form-row"><div class="field"><label>Name</label><input id="n" value="${esc(d.name || "")}"></div><div class="field"><label>Queue prefix</label><input id="p" maxlength="4" value="${esc(d.prefix || "")}"></div></div>
        <div class="field"><label>Description</label><input id="ds" value="${esc(d.description || "")}"></div>
        <label class="chk"><input type="checkbox" id="ac" ${d.active === 0 ? "" : "checked"}> Active (accepting appointments)</label>
        <div class="btn-row" style="margin-top:16px"><button class="btn" id="sv">Save</button><button class="btn secondary" id="cx">Cancel</button></div>`);
      $("#cx", m.el).onclick = m.close;
      $("#sv", m.el).onclick = (e) => busy(e.currentTarget, "Saving…", async () => {
        const body = { name: $("#n", m.el).value, prefix: $("#p", m.el).value, description: $("#ds", m.el).value, active: $("#ac", m.el).checked ? 1 : 0 };
        try { d.id ? await API.put(`/api/admin/departments/${d.id}`, body) : await API.post("/api/admin/departments", body); m.close(); toast("Department saved"); draw(); }
        catch (err) { $("#mm", m.el).innerHTML = errorBox(err.message); }
      });
    };
    $("#add").onclick = () => open();
    $$("[data-e]").forEach((b) => b.onclick = () => open(r.departments.find((x) => x.id == b.dataset.e)));
  };
  await draw();
});

/* -------- doctors -------- */
App.route("/admin/doctors", "ADMIN", async (el) => {
  const deps = (await API.get("/api/admin/departments")).departments;
  const draw = async () => {
    const r = await API.get("/api/admin/doctors");
    el.innerHTML = `<div class="page-head"><div><h1>Doctors & Schedules</h1><p>Working days, consulting hours and average consultation duration.</p></div><button class="btn" id="add">＋ Add doctor</button></div>
      <div class="card"><div class="table-wrap"><table><tr><th>Doctor</th><th>Department</th><th>Specialization</th><th>Working days</th><th>Hours</th><th class="num">Avg consult</th><th>Status</th><th></th></tr>
      ${r.doctors.map((d) => `<tr><td><strong>${esc(d.name)}</strong></td><td>${esc(d.department_name)}</td><td>${esc(d.specialization || "")}</td><td>${d.working_days.split(",").map((x) => fmt.day(+x)).join(", ")}</td>
        <td>${d.start_time}–${d.end_time}</td><td class="num">${fmt.n(d.consultation_duration_average, 0)} min</td><td>${d.active ? fmt.status("ACTIVE") : `<span class="badge NO_SHOW">INACTIVE</span>`}</td>
        <td class="num"><button class="btn secondary sm" data-e="${d.id}">Edit</button></td></tr>`).join("")}</table></div></div>`;
    const open = (d = {}) => {
      const wd = (d.working_days || "0,1,2,3,4,5").split(",").map(Number);
      const m = modal(`<h2>${d.id ? "Edit" : "Add"} doctor</h2><div id="mm"></div>
        <div class="field"><label>Name</label><input id="n" value="${esc(d.name || "Dr. ")}"></div>
        <div class="form-row"><div class="field"><label>Department</label><select id="dp">${deps.map((x) => `<option value="${x.id}" ${x.id == d.department_id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></div>
          <div class="field"><label>Specialization</label><input id="sp" value="${esc(d.specialization || "")}"></div></div>
        <div class="field"><label>Working days</label>${DAYS.map((i) => `<label class="chk"><input type="checkbox" class="wd" value="${i}" ${wd.includes(i) ? "checked" : ""}>${fmt.day(i)}</label>`).join("")}</div>
        <div class="form-row"><div class="field"><label>Start time</label><input type="time" id="st" value="${d.start_time || "09:00"}"></div><div class="field"><label>End time</label><input type="time" id="en" value="${d.end_time || "13:00"}"></div></div>
        <div class="field"><label>Average consultation duration (min)</label><input type="number" id="du" min="1" max="120" value="${d.consultation_duration_average || 12}"><div class="hint">Used as a model feature and by the baseline.</div></div>
        <label class="chk"><input type="checkbox" id="ac" ${d.active === 0 ? "" : "checked"}> Active</label>
        <div class="btn-row" style="margin-top:16px"><button class="btn" id="sv">Save</button><button class="btn secondary" id="cx">Cancel</button></div>`);
      $("#cx", m.el).onclick = m.close;
      $("#sv", m.el).onclick = (e) => busy(e.currentTarget, "Saving…", async () => {
        const body = { name: $("#n", m.el).value, department_id: +$("#dp", m.el).value, specialization: $("#sp", m.el).value,
          working_days: $$(".wd", m.el).filter((c) => c.checked).map((c) => +c.value), start_time: $("#st", m.el).value, end_time: $("#en", m.el).value,
          consultation_duration_average: +$("#du", m.el).value, active: $("#ac", m.el).checked ? 1 : 0 };
        try { d.id ? await API.put(`/api/admin/doctors/${d.id}`, body) : await API.post("/api/admin/doctors", body); m.close(); toast("Doctor saved"); draw(); }
        catch (err) { $("#mm", m.el).innerHTML = errorBox(err.message); }
      });
    };
    $("#add").onclick = () => open();
    $$("[data-e]").forEach((b) => b.onclick = () => open(r.doctors.find((x) => x.id == b.dataset.e)));
  };
  await draw();
});

/* -------- staff -------- */
App.route("/admin/staff", "ADMIN", async (el) => {
  const deps = (await API.get("/api/admin/departments")).departments;
  const depOpts = (sel) => `<option value="">All departments</option>` + deps.map((x) => `<option value="${x.id}" ${x.id == sel ? "selected" : ""}>${esc(x.name)}</option>`).join("");
  const draw = async () => {
    const r = await API.get("/api/admin/staff");
    el.innerHTML = `<div class="page-head"><div><h1>Staff</h1><p>Staff accounts can manage queues in their assigned department (or all).</p></div><button class="btn" id="add">＋ Add staff</button></div>
      <div class="card"><div class="table-wrap"><table><tr><th>Name</th><th>Email</th><th>Role</th><th>Department scope</th><th>Status</th><th></th></tr>
      ${r.staff.map((u) => `<tr><td><strong>${esc(u.full_name)}</strong></td><td>${esc(u.email)}</td><td><span class="badge">${u.role}</span></td>
        <td>${u.role === "STAFF" ? `<select data-dep="${u.id}" style="max-width:200px">${depOpts(u.department_id)}</select>` : "All (admin)"}</td>
        <td>${u.active ? fmt.status("ACTIVE") : `<span class="badge NO_SHOW">INACTIVE</span>`}</td>
        <td class="num">${u.id !== App.user.id ? `<button class="btn ${u.active ? "danger" : "secondary"} sm" data-t="${u.id}" data-a="${u.active ? 0 : 1}">${u.active ? "Deactivate" : "Activate"}</button>` : ""}</td></tr>`).join("")}</table></div></div>`;
    $$("[data-dep]").forEach((s) => s.onchange = async () => { try { await API.put(`/api/admin/staff/${s.dataset.dep}`, { department_id: s.value ? +s.value : null }); toast("Scope updated"); } catch (e) { toast(e.message, "error"); } });
    $$("[data-t]").forEach((b) => b.onclick = () => busy(b, "…", async () => { try { await API.put(`/api/admin/staff/${b.dataset.t}`, { active: +b.dataset.a }); draw(); } catch (e) { toast(e.message, "error"); } }));
    $("#add").onclick = () => {
      const m = modal(`<h2>Add staff account</h2><div id="mm"></div>
        <div class="field"><label>Full name</label><input id="n"></div><div class="field"><label>Email</label><input id="e" type="email"></div>
        <div class="field"><label>Temporary password</label><input id="p" type="password"><div class="hint">At least 8 characters with letters and numbers.</div></div>
        <div class="form-row"><div class="field"><label>Role</label><select id="r"><option>STAFF</option><option>ADMIN</option></select></div><div class="field"><label>Department scope</label><select id="d">${depOpts()}</select></div></div>
        <div class="btn-row"><button class="btn" id="sv">Create</button><button class="btn secondary" id="cx">Cancel</button></div>`);
      $("#cx", m.el).onclick = m.close;
      $("#sv", m.el).onclick = (e) => busy(e.currentTarget, "Creating…", async () => {
        try { await API.post("/api/admin/staff", { full_name: $("#n", m.el).value, email: $("#e", m.el).value, password: $("#p", m.el).value, role: $("#r", m.el).value, department_id: $("#d", m.el).value ? +$("#d", m.el).value : null }); m.close(); toast("Staff account created"); draw(); }
        catch (err) { $("#mm", m.el).innerHTML = errorBox(err.message); }
      });
    };
  };
  await draw();
});

/* -------- appointments -------- */
App.route("/admin/appointments", "ADMIN", async (el) => {
  const deps = (await API.get("/api/admin/departments")).departments;
  el.innerHTML = `<div class="page-head"><div><h1>All Appointments</h1><p id="cnt"></p></div></div>
    <div class="card"><div class="form-row four">
      <div class="field"><label>Date</label><input type="date" id="fd" value="${todayISO()}"></div>
      <div class="field"><label>Status</label><select id="fs"><option value="">All</option>${["BOOKED", "CONFIRMED", "COMPLETED", "CANCELLED", "NO_SHOW"].map((s) => `<option>${s}</option>`).join("")}</select></div>
      <div class="field"><label>Department</label><select id="fp"><option value="">All</option>${deps.map((d) => `<option value="${d.id}">${esc(d.name)}</option>`).join("")}</select></div>
      <div class="field" style="align-self:end"><button class="btn secondary" id="clr">Clear date</button></div></div><div id="tbl"></div></div>`;
  const load = async () => {
    $("#tbl").innerHTML = loading("Loading appointments…");
    const q = new URLSearchParams({ date: $("#fd").value, status: $("#fs").value, department_id: $("#fp").value, limit: 300 });
    const r = await API.get(`/api/admin/appointments?${q}`);
    $("#cnt").textContent = `${r.total} matching · showing ${r.appointments.length}`;
    $("#tbl").innerHTML = r.appointments.length ? `<div class="table-wrap"><table><tr><th>Date</th><th>Time</th><th>Queue no.</th><th>Patient</th><th>Doctor</th><th>Department</th><th>Status</th><th>Booked</th></tr>
      ${r.appointments.map((a) => `<tr><td>${fmt.date(a.appointment_date)}</td><td>${esc(a.appointment_time)}</td><td><strong>${esc(a.queue_number || "–")}</strong></td><td>${esc(a.patient_name)}</td><td>${esc(a.doctor_name)}</td><td>${esc(a.department_name)}</td><td>${fmt.status(a.status)}</td><td><small>${fmt.dt(a.booking_timestamp)}</small></td></tr>`).join("")}</table></div>` : empty("No appointments match these filters.");
  };
  ["#fd", "#fs", "#fp"].forEach((s) => $(s).onchange = load);
  $("#clr").onclick = () => { $("#fd").value = ""; load(); };
  await load();
});

App.route("/admin/queues", "ADMIN", async (el) => {
  await App.routes["/staff/manage"].render(el);
  $("h1", el).textContent = "All Queues";
});

/* -------- queue history -------- */
App.route("/admin/history", "ADMIN", async (el) => {
  let page = 1, source = "";
  const load = async () => {
    const r = await API.get(`/api/admin/history?page=${page}${source ? "&source=" + source : ""}`);
    const pages = Math.max(1, Math.ceil(r.total / r.page_size));
    el.innerHTML = `<div class="page-head"><div><h1>Queue History</h1><p>The ML training dataset · ${r.total} records. One row per completed consultation.</p></div>
        <div class="btn-row"><select id="src" style="max-width:200px"><option value="">All sources</option>${["LIVE", "SYNTHETIC", "SYNTHETIC_INVALID"].map((s) => `<option ${s === source ? "selected" : ""}>${s}</option>`).join("")}</select>
        <a class="btn secondary" href="/api/admin/history/export.csv">⇩ Export CSV</a></div></div>
      <div class="card"><div class="table-wrap"><table><tr><th>Date</th><th>Doctor</th><th>Dept</th><th class="num">Hour</th><th class="num">Ahead</th><th class="num">Queue len</th><th class="num">Avg consult</th><th class="num">Arrival delay</th><th class="num">Prev avg wait</th><th class="num">Actual wait (target)</th><th>Source</th></tr>
      ${r.records.map((h) => `<tr><td>${esc(h.date)}</td><td>${esc(h.doctor_name || "–")}</td><td>${esc(h.department_name || "–")}</td><td class="num">${h.appointment_hour ?? "–"}:${String(h.appointment_minute ?? "").padStart(2, "0")}</td>
        <td class="num">${h.patients_ahead ?? "∅"}</td><td class="num">${h.queue_length ?? "∅"}</td><td class="num">${fmt.n(h.consultation_duration_average, 0)}</td><td class="num">${fmt.n(h.patient_arrival_delay)}</td><td class="num">${fmt.n(h.previous_average_wait)}</td>
        <td class="num"><strong>${h.actual_waiting_time == null ? "∅" : fmt.n(h.actual_waiting_time)}</strong></td><td><span class="badge ${h.source === "LIVE" ? "ok" : h.source === "SYNTHETIC_INVALID" ? "FAILED" : ""}">${h.source}</span></td></tr>`).join("")}</table></div>
      <div class="btn-row" style="margin-top:12px;align-items:center"><button class="btn secondary sm" id="pv" ${page <= 1 ? "disabled" : ""}>← Prev</button><small>Page ${page} of ${pages}</small><button class="btn secondary sm" id="nx" ${page >= pages ? "disabled" : ""}>Next →</button></div></div>
      <div class="notice">SYNTHETIC rows come from the seeded OPD simulation; LIVE rows are recorded automatically whenever staff complete a consultation; SYNTHETIC_INVALID rows were deliberately corrupted to demonstrate data validation. ${PRIVACY}</div>`;
    $("#pv").onclick = () => { page--; load(); };
    $("#nx").onclick = () => { page++; load(); };
    $("#src").onchange = () => { source = $("#src").value; page = 1; load(); };
  };
  await load();
});

/* -------- training data quality -------- */
function dqCard(dq) {
  return `<div class="grid g4">${[["Total records", dq.total_records], ["Missing values", dq.missing_values], ["Invalid values", dq.invalid_values], ["Duplicates", dq.duplicate_records]]
    .map(([l, v]) => `<div class="card stat"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>
    <div class="card" style="margin-top:16px"><div class="card-head"><h3>Valid training records: ${dq.valid_records}</h3>${dq.sufficient ? `<span class="badge ok">Sufficient (min ${dq.minimum_required})</span>` : `<span class="badge FAILED">Insufficient (min ${dq.minimum_required})</span>`}</div>
    ${dq.exclusions.length ? `<div class="table-wrap"><table><tr><th>Excluded because</th><th class="num">Records</th><th>Example record IDs</th></tr>${dq.exclusions.map((x) => `<tr><td>${esc(x.reason)}</td><td class="num">${x.count}</td><td>${x.example_ids.join(", ")}</td></tr>`).join("")}</table></div>` : `<p>No records need to be excluded.</p>`}
    <p class="hint" style="margin-top:10px">Chronological split: oldest days train (${dq.expected_training_records} records, ${dq.training_percentage}%), most recent days test (${dq.expected_testing_records} records, ${dq.testing_percentage}%)${dq.test_period_starts ? ", test period starts " + esc(dq.test_period_starts) : ""}.</p></div>`;
}
App.route("/admin/training-data", "ADMIN", async (el) => {
  const dq = await API.get("/api/admin/data-quality");
  el.innerHTML = `<div class="page-head"><div><h1>Training Data</h1><p>Validation of the QueueHistory dataset before training.</p></div>
      <div class="btn-row"><a class="btn secondary" href="#/admin/history">Browse records</a><a class="btn secondary" href="/api/admin/history/export.csv">⇩ Export CSV</a></div></div>
    ${dqCard(dq)}
    <div class="card"><h3>Model inputs (${dq.feature_count}) and target</h3>
      <div>${dq.features.map((f) => `<span class="badge" style="margin:3px">${esc(f)}</span>`).join("")}</div>
      <p style="margin-top:10px">Target: <span class="badge ai">${esc(dq.target)}</span> (minutes, check-in → consultation start). Excluded from inputs to prevent leakage / for privacy: actual_waiting_time, the patient's own consultation_duration, and all personal fields (name, email, phone, patient id).</p></div>`;
});

/* -------- ML model -------- */
App.route("/admin/ml", "ADMIN", async (el) => {
  const [modelsR, dq, mon] = await Promise.all([API.get("/api/admin/models"), API.get("/api/admin/data-quality"), API.get("/api/admin/predictions?limit=30")]);
  const models = modelsR.models;
  const active = models.find((m) => m.active);
  const detail = active ? (await API.get(`/api/admin/models/${active.id}`)).model : null;
  const md = detail?.metadata;
  const imp = md ? md.feature_importance.map((f) => ({ label: f.feature, value: f.importance * 100 })) : [];
  const hist = md ? md.error_histogram.counts.map((c, i) => ({ label: `${Math.round(md.error_histogram.edges[i])}`, value: c })) : [];

  el.innerHTML = `<div class="page-head"><div><h1>AI / ML Model</h1><p>Random Forest regression model that predicts outpatient waiting time (minutes).</p></div></div>
    ${active ? `<div class="grid g2">
      <div class="card"><div class="card-head"><h3>Model overview</h3><span class="badge ACTIVE">ACTIVE</span></div>
        <dl class="kv"><dt>Model name</dt><dd>${esc(detail.model_name)}</dd><dt>Algorithm</dt><dd>${esc(detail.model_type)} (scikit-learn)</dd><dt>Version</dt><dd>v${detail.version}</dd>
          <dt>Status</dt><dd>${detail.training_status}</dd><dt>Training date</dt><dd>${fmt.dt(detail.training_date)}</dd>
          <dt>Hyper-parameters</dt><dd style="font-weight:500;font-size:.85rem">${Object.entries(md.algorithm_params).filter(([k]) => k !== "n_jobs").map(([k, v]) => `${k}=${v}`).join(", ")}</dd></dl></div>
      <div class="card"><h3>Dataset</h3><dl class="kv"><dt>Total records</dt><dd>${md.data_quality.total_records}</dd><dt>Valid records</dt><dd>${detail.dataset_size}</dd>
          <dt>Split</dt><dd>Chronological (time-based)</dd><dt>Training records</dt><dd>${detail.training_records} · ${esc(md.split.train_period[0])} → ${esc(md.split.train_period[1])}</dd><dt>Testing records</dt><dd>${detail.testing_records} · ${esc(md.split.test_period[0])} → ${esc(md.split.test_period[1])}</dd><dt>Feature count</dt><dd>${detail.feature_count}</dd>
          <dt>Encoding</dt><dd style="font-weight:500;font-size:.85rem">${esc(md.encoding)}</dd>${md.trained_on_data_before ? `<dt>Data window</dt><dd>records before ${esc(md.trained_on_data_before)}</dd>` : ""}</dl></div></div>
    <div class="card"><h3>Evaluation on the most recent, unseen days (chronological test set)</h3>
      <div class="grid g4">
        <div class="stat"><div class="label">MAE</div><div class="value">${fmt.n(detail.mae, 2)}<small> min</small></div><div class="sub">mean absolute error</div></div>
        <div class="stat"><div class="label">RMSE</div><div class="value">${fmt.n(detail.rmse, 2)}<small> min</small></div><div class="sub">root mean squared error</div></div>
        <div class="stat"><div class="label">R²</div><div class="value">${fmt.n(detail.r2_score, 3)}</div><div class="sub">variance explained</div></div>
        <div class="stat"><div class="label">Time-series CV MAE</div><div class="value">${md.cross_validation ? fmt.n(md.cross_validation.mae_mean, 2) : "–"}<small> min</small></div><div class="sub">${md.cross_validation ? "± " + fmt.n(md.cross_validation.mae_std, 2) + " · 5 forward folds" : "needs ≥200 training records"}</div></div></div>
      <div class="table-wrap" style="margin-top:14px"><table><tr><th>Method</th><th class="num">MAE</th><th class="num">RMSE</th><th class="num">R²</th></tr>
        <tr><td><strong>Random Forest (ML)</strong></td><td class="num">${fmt.n(md.test_metrics.mae, 2)}</td><td class="num">${fmt.n(md.test_metrics.rmse, 2)}</td><td class="num">${fmt.n(md.test_metrics.r2, 3)}</td></tr>
        <tr><td>Baseline: ${esc(md.baseline.description)}</td><td class="num">${fmt.n(md.baseline.mae, 2)}</td><td class="num">${fmt.n(md.baseline.rmse, 2)}</td><td class="num">${fmt.n(md.baseline.r2, 3)}</td></tr></table></div>
      <p style="margin-top:10px"><strong>ML improvement over baseline (MAE): ${fmt.pct(md.improvement_over_baseline_pct)}</strong> · Training-set MAE ${fmt.n(md.train_metrics.mae, 2)} (vs test ${fmt.n(md.test_metrics.mae, 2)}) shows the generalisation gap.</p></div>
    <div class="grid g2">
      <div class="card"><h3>Actual vs predicted (test set)</h3>${Charts.scatter(md.test_samples.map((s) => ({ x: s.actual, y: s.predicted })))}</div>
      <div class="card"><h3>Error distribution (predicted − actual, min)</h3>${Charts.columns(hist, { color: "#5b3fc4", labelEvery: 2, yLabel: "records" })}</div>
      <div class="card span2"><h3>Feature importance</h3><p class="hint">Mean decrease in impurity, one-hot columns summed back to their original feature.</p>${Charts.hbars(imp, { unit: "%", digits: 1, color: "#1d5fd1" })}</div>
    </div>` : `<div class="alert warn">No model is active. Train a model and activate it to enable patient predictions.</div>`}

    <div class="card" id="train-card"><div class="card-head"><h3>Train a new model version</h3></div>
      <div class="grid g4">${[["Total records", dq.total_records], ["Valid records", dq.valid_records], ["Training", dq.training_percentage + "%"], ["Testing", dq.testing_percentage + "%"]].map(([l, v]) => `<div class="stat"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>
      <p class="hint">${dq.total_records - dq.valid_records} record(s) will be excluded by validation. <a href="#/admin/training-data">Details</a></p>
      <div id="tmsg"></div><button class="btn lg" id="train" ${dq.sufficient ? "" : "disabled"}>TRAIN MODEL</button></div>

    <div class="card"><h3>Model versions</h3><div class="table-wrap"><table><tr><th>Version</th><th>Trained</th><th>Status</th><th class="num">Records</th><th class="num">MAE</th><th class="num">RMSE</th><th class="num">R²</th><th class="num">Baseline MAE</th><th class="num">Δ vs baseline</th><th></th></tr>
      ${models.map((m) => `<tr><td><strong>${esc(m.model_name)} v${m.version}</strong></td><td>${fmt.dt(m.training_date)}</td><td>${fmt.status(m.training_status)} ${m.active ? `<span class="badge ACTIVE">ACTIVE</span>` : ""}${m.error ? `<br><small class="muted">${esc(m.error)}</small>` : ""}</td>
        <td class="num">${m.dataset_size ?? "–"}</td><td class="num">${fmt.n(m.mae, 2)}</td><td class="num">${fmt.n(m.rmse, 2)}</td><td class="num">${fmt.n(m.r2_score, 3)}</td><td class="num">${fmt.n(m.baseline_mae, 2)}</td><td class="num">${m.improvement_over_baseline_pct != null ? fmt.pct(m.improvement_over_baseline_pct) : "–"}</td>
        <td class="num">${m.training_status === "TRAINED" && !m.active ? `<button class="btn teal sm" data-act="${m.id}">ACTIVATE MODEL</button>` : ""}</td></tr>`).join("")}</table></div></div>

    <div class="card"><div class="card-head"><h3>Prediction performance monitoring</h3><small class="muted">Live MAE ${fmt.min(mon.summary.live_mae)} over ${mon.summary.n} completed predictions · baseline ${fmt.min(mon.summary.baseline_live_mae)}</small></div>
      <div class="table-wrap"><table><tr><th>Queue no.</th><th>Doctor</th><th>Predicted at</th><th>Model</th><th class="num">Predicted</th><th class="num">Actual</th><th class="num">Error</th></tr>
      ${mon.recent.map((p) => `<tr><td>${esc(p.queue_number)}</td><td>${esc(p.doctor_name)}</td><td>${fmt.dt(p.prediction_timestamp)}</td><td>v${p.model_version}</td><td class="num">${fmt.n(p.predicted_waiting_time)} min</td>
        <td class="num">${p.actual_waiting_time != null ? fmt.n(p.actual_waiting_time) + " min" : `<span class="badge WAITING">pending</span>`}</td><td class="num">${p.absolute_error != null ? fmt.n(p.absolute_error) + " min" : "–"}</td></tr>`).join("")}</table></div></div>`;

  $("#train").onclick = (e) => busy(e.currentTarget, "Training model…", async () => {
    $("#tmsg").innerHTML = `<div class="alert info">Training model… validating data, fitting the Random Forest and evaluating on the most recent days.</div>`;
    try {
      const m = (await API.post("/api/admin/models/train")).model;
      $("#tmsg").innerHTML = `<div class="alert success"><strong>${esc(m.model_name)} v${m.version} trained.</strong> Dataset ${m.dataset_size} records (${m.training_records} train / ${m.testing_records} test) ·
        MAE ${fmt.n(m.mae, 2)} min · RMSE ${fmt.n(m.rmse, 2)} min · R² ${fmt.n(m.r2_score, 3)} · ${fmt.pct(m.improvement_over_baseline_pct)} better than baseline.
        <div style="margin-top:10px"><button class="btn teal sm" id="actnew">ACTIVATE MODEL v${m.version}</button></div></div>`;
      $("#actnew").onclick = (ev) => busy(ev.currentTarget, "Activating…", async () => { try { await API.post(`/api/admin/models/${m.id}/activate`); toast(`v${m.version} is now active`); App.render(); } catch (err) { toast(err.message, "error"); } });
    } catch (err) {
      const m = err.data && err.data.model;
      $("#tmsg").innerHTML = errorBox(m && m.error ? `Training failed: ${m.error} The active model was not changed.` : err.message);
    }
  });
  $$("[data-act]").forEach((b) => b.onclick = () => busy(b, "Activating…", async () => { try { await API.post(`/api/admin/models/${b.dataset.act}/activate`); toast("Model activated"); App.render(); } catch (err) { toast(err.message, "error"); } }));
});

App.route("/admin/analytics", "ADMIN", (el) => analyticsPage(el, "/api/admin/analytics"));

/* -------- settings -------- */
App.route("/admin/settings", "ADMIN", async (el) => {
  const s = (await API.get("/api/admin/settings")).settings;
  el.innerHTML = `<div class="page-head"><div><h1>Settings</h1><p>System configuration.</p></div></div>
    <div class="card" style="max-width:720px"><div id="msg"></div>
      <div class="field"><label>Hospital name</label><input id="hospital_name" value="${esc(s.hospital_name)}"></div>
      <div class="form-row"><div class="field"><label>Slot length (minutes)</label><select id="slot_minutes">${[5, 10, 15, 20, 30].map((v) => `<option ${v == s.slot_minutes ? "selected" : ""}>${v}</option>`).join("")}</select></div>
        <div class="field"><label>Peak hours (comma-separated)</label><input id="peak_hours" value="${esc(s.peak_hours)}"><div class="hint">Used for the peak_hour model feature.</div></div></div>
      <div class="form-row"><div class="field"><label>Re-predict after (minutes)</label><input type="number" id="repredict_minutes" value="${esc(s.repredict_minutes)}"><div class="hint">A new prediction is also made whenever the queue changes.</div></div>
        <div class="field"><label>Max days ahead for booking</label><input type="number" id="max_booking_days_ahead" value="${esc(s.max_booking_days_ahead)}"></div></div>
      <div class="form-row"><div class="field"><label>Patient cancellation cut-off (minutes before slot)</label><input type="number" id="cancellation_cutoff_minutes" value="${esc(s.cancellation_cutoff_minutes)}"></div>
        <div class="field"><label>Demo mode</label><select id="demo_mode"><option value="1" ${s.demo_mode == "1" ? "selected" : ""}>On — earlier slots today stay bookable</option><option value="0" ${s.demo_mode == "0" ? "selected" : ""}>Off — real-time slot rules</option></select></div></div>
      <button class="btn" id="sv">Save settings</button></div>
    <div class="notice">${PRIVACY}</div>`;
  $("#sv").onclick = (e) => busy(e.currentTarget, "Saving…", async () => {
    const body = {}; ["hospital_name", "slot_minutes", "peak_hours", "repredict_minutes", "max_booking_days_ahead", "cancellation_cutoff_minutes", "demo_mode"].forEach((k) => body[k] = $("#" + k).value);
    try { await API.put("/api/admin/settings", body); $("#msg").innerHTML = `<div class="alert success">Settings saved.</div>`; }
    catch (err) { $("#msg").innerHTML = errorBox(err.message); }
  });
});
