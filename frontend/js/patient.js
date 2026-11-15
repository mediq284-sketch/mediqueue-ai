/* ============================ PATIENT PAGES ============================ */
function etaCard(pred, compact = false) {
  if (!pred) return "";
  if (pred.status !== "ok") {
    return `<div class="card eta"><h3>AI Estimated Waiting Time</h3><p style="font-size:1.05rem;margin:10px 0 0">${esc(pred.message)}</p></div>`;
  }
  const p = pred.prediction;
  return `<div class="card eta"><div style="display:flex;justify-content:space-between;align-items:center">
      <h3 style="margin:0">AI Estimated Waiting Time</h3><span class="badge" style="background:rgba(255,255,255,.18);color:#fff">ML prediction</span></div>
    <div style="margin-top:14px"><span class="big">${Math.round(p.predicted_waiting_time)}</span> <span class="unit">min</span></div>
    <div class="meta">Estimated waiting time · predicted ${fmt.time(p.prediction_timestamp)} · model ${esc(p.model_version)}</div>
    ${compact ? "" : `<div class="disclaimer">${DISCLAIMER}</div>`}</div>`;
}

App.route("/patient", "PATIENT", async (el) => {
  const [dash, q] = await Promise.all([API.get("/api/patient/dashboard"), API.get("/api/patient/queue").catch(() => ({ queue: undefined }))]);
  const today = q.queue;
  const todayCard = today ? `<div class="card"><div class="card-head"><h3>Today's Appointment</h3>${fmt.status(today.queue_entry.status)}</div>
      <div class="qnum">${esc(today.queue_entry.queue_number)}</div>
      <dl class="kv" style="margin-top:10px"><dt>Doctor</dt><dd>${esc(today.appointment.doctor_name)}</dd><dt>Department</dt><dd>${esc(today.appointment.department_name)}</dd>
      <dt>Appointment time</dt><dd>${esc(today.appointment.appointment_time)}</dd><dt>Patients ahead</dt><dd>${today.patients_ahead}</dd>
      <dt>Now serving</dt><dd>${esc(today.now_serving || "–")}</dd></dl>
      <a class="btn" href="#/patient/queue" style="margin-top:14px;color:#fff">Track my queue</a></div>`
    : `<div class="card"><h3>Today's Appointment</h3>${q.queue === undefined ? errorBox("Queue information is temporarily unavailable.") : `<p>You have no appointment today.</p><a class="btn" href="#/patient/book" style="color:#fff">Book an appointment</a>`}</div>`;
  el.innerHTML = `<div class="page-head"><div><h1>Hello, ${esc(App.user.full_name.split(" ")[0])}</h1><p>Your appointments and live queue at a glance.</p></div>
      <a class="btn" href="#/patient/book" style="color:#fff">＋ Book appointment</a></div>
    <div class="hero-q">${todayCard}${today ? etaCard(today.prediction, true) : `<div class="card"><h3>AI Estimated Waiting Time</h3><p>Your ML-based waiting-time estimate appears here on the day of your appointment.</p></div>`}</div>
    <div class="card"><div class="card-head"><h3>Upcoming appointments</h3><a href="#/patient/appointments">View all</a></div>
      ${dash.upcoming.length ? `<div class="table-wrap"><table><tr><th>Date</th><th>Time</th><th>Doctor</th><th>Department</th><th>Queue no.</th><th>Status</th></tr>
      ${dash.upcoming.map((a) => `<tr><td>${fmt.date(a.appointment_date)}</td><td>${esc(a.appointment_time)}</td><td>${esc(a.doctor_name)}</td><td>${esc(a.department_name)}</td><td><strong>${esc(a.queue_number)}</strong></td><td>${fmt.status(a.status)}</td></tr>`).join("")}</table></div>`
      : empty("No upcoming appointments.")}</div>
    <div class="grid g4" style="margin-top:16px">${["CONFIRMED", "COMPLETED", "CANCELLED", "NO_SHOW"].map((s) => `<div class="card stat"><div class="label">${s.replace("_", " ")}</div><div class="value">${dash.counts[s] || 0}</div></div>`).join("")}</div>
    <div class="notice">${PRIVACY}</div>`;
});

App.route("/patient/book", "PATIENT", async (el) => {
  const st = { dep: null, doc: null, date: todayISO(), time: null, deps: [], docs: [] };
  const info = await API.get("/api/public/info");
  st.deps = (await API.get("/api/departments")).departments;
  const pre = new URLSearchParams(location.hash.split("?")[1] || "").get("doctor");
  if (pre) {   // deep link from the Doctors page: preselect department + doctor
    const all = (await API.get("/api/doctors")).doctors;
    const d = all.find((x) => x.id == pre);
    if (d) { st.dep = st.deps.find((x) => x.id == d.department_id) || null; st.docs = all.filter((x) => x.department_id == d.department_id); st.doc = st.dep ? d : null; }
  }
  el.innerHTML = `<div class="page-head"><div><h1>Book Appointment</h1><p>Choose a department, doctor, date and time slot.</p></div></div>
    <div class="steps" id="steps"></div><div id="msg"></div><div class="card" id="body"></div>`;
  const steps = ["Department", "Doctor", "Date & slot", "Confirm"];
  const stepNo = () => (!st.dep ? 0 : !st.doc ? 1 : !st.time ? 2 : 3);
  const drawSteps = () => { const n = stepNo(); $("#steps").innerHTML = steps.map((s, i) => `<div class="step ${i < n ? "done" : i === n ? "on" : ""}">${i + 1}. ${s}</div>`).join(""); };
  const back = (label, fn) => `<button class="btn secondary sm" id="back">← ${label}</button>`;

  async function draw() {
    drawSteps(); $("#msg").innerHTML = "";
    const body = $("#body");
    if (!st.dep) {
      body.innerHTML = `<h3>Select a department</h3><div class="choice-grid">${st.deps.map((d) => `<button class="choice" data-id="${d.id}"><h4>${esc(d.name)}</h4><p class="muted">${esc(d.description || "")}</p><p style="margin-top:6px"><span class="badge">${esc(d.prefix)}</span> <small>${d.doctor_count} doctor(s)</small></p></button>`).join("")}</div>`;
      $$(".choice", body).forEach((b) => b.onclick = async () => { st.dep = st.deps.find((d) => d.id == b.dataset.id); st.docs = (await API.get(`/api/doctors?department_id=${st.dep.id}`)).doctors; draw(); });
    } else if (!st.doc) {
      body.innerHTML = `<div class="card-head"><h3>Select a doctor · ${esc(st.dep.name)}</h3>${back("Departments")}</div>
        ${st.docs.length ? `<div class="choice-grid">${st.docs.map((d) => `<button class="choice" data-id="${d.id}"><h4>${esc(d.name)}</h4><p class="muted">${esc(d.specialization || "")}</p>
        <p style="margin-top:6px"><small>${d.working_days.split(",").map((x) => fmt.day(+x)).join(", ")} · ${d.start_time}–${d.end_time}</small></p></button>`).join("")}</div>` : empty("No doctors available in this department.")}`;
      $("#back").onclick = () => { st.dep = null; draw(); };
      $$(".choice", body).forEach((b) => b.onclick = () => { st.doc = st.docs.find((d) => d.id == b.dataset.id); draw(); });
    } else if (!st.time) {
      body.innerHTML = `<div class="card-head"><h3>Choose date & time · ${esc(st.doc.name)}</h3>${back("Doctors")}</div>
        <div class="field" style="max-width:240px"><label for="dt">Date</label><input type="date" id="dt" min="${todayISO()}" value="${st.date}"></div>
        <p class="hint">Consults ${st.doc.working_days.split(",").map((x) => fmt.day(+x)).join(", ")}, ${st.doc.start_time}–${st.doc.end_time}.${info.demo_mode ? " Demo mode: earlier slots today remain bookable for live demonstrations." : ""}</p>
        <div id="slots">${loading("Loading available slots…")}</div>`;
      $("#back").onclick = () => { st.doc = null; draw(); };
      const loadSlots = async () => {
        st.date = $("#dt").value;
        if (!st.date || st.date < todayISO()) { $("#slots").innerHTML = errorBox("Please choose today or a future date."); return; }
        $("#slots").innerHTML = loading("Loading available slots…");
        try {
          const r = await API.get(`/api/doctors/${st.doc.id}/slots?date=${st.date}`);
          const free = r.slots.filter((s) => s.available).length;
          $("#slots").innerHTML = `<p><strong>${free}</strong> of ${r.slots.length} slots available</p><div class="slots">${r.slots.map((s) => `<button class="slot" ${s.available ? "" : "disabled"} data-t="${s.time}">${s.time}</button>`).join("")}</div>`;
          $$(".slot").forEach((b) => b.onclick = () => { st.time = b.dataset.t; draw(); });
        } catch (e) { $("#slots").innerHTML = errorBox(e.message); }
      };
      $("#dt").onchange = loadSlots; loadSlots();
    } else {
      body.innerHTML = `<div class="card-head"><h3>Confirm your appointment</h3>${back("Change time")}</div>
        <dl class="kv"><dt>Department</dt><dd>${esc(st.dep.name)}</dd><dt>Doctor</dt><dd>${esc(st.doc.name)}</dd><dt>Date</dt><dd>${fmt.date(st.date)}</dd><dt>Time</dt><dd>${esc(st.time)}</dd></dl>
        <p class="hint" style="margin-top:12px">Your queue number is generated as soon as you confirm.${st.date === todayISO() ? " Because this is a same-day booking you join the live queue immediately." : ""}</p>
        <button class="btn lg" id="confirm" style="margin-top:6px">Confirm booking</button>`;
      $("#back").onclick = () => { st.time = null; draw(); };
      $("#confirm").onclick = (e) => busy(e.currentTarget, "Booking…", async () => {
        try {
          const a = (await API.post("/api/patient/appointments", { doctor_id: st.doc.id, date: st.date, time: st.time })).appointment;
          drawSteps(); $$(".step").forEach((s) => s.className = "step done");
          body.innerHTML = `<div style="text-align:center;padding:10px"><span class="badge COMPLETED">Appointment confirmed</span>
            <p style="margin-top:16px">Your queue number</p><div class="qnum" style="font-size:3.2rem">${esc(a.queue_number)}</div>
            <p>${esc(st.doc.name)} · ${esc(st.dep.name)} · ${fmt.date(a.appointment_date)} at ${esc(a.appointment_time)}</p>
            <div class="btn-row" style="justify-content:center;margin-top:16px">${a.appointment_date === todayISO() ? `<a class="btn" style="color:#fff" href="#/patient/queue">Track my queue</a>` : ""}<a class="btn secondary" href="#/patient/appointments">My appointments</a></div></div>`;
          toast(`Booked · ${a.queue_number}`);
        } catch (err) { $("#msg").innerHTML = errorBox(err.message); if (err.status === 409) { st.time = null; setTimeout(draw, 1600); } }
      });
    }
  }
  draw();
});

App.route("/patient/queue", "PATIENT", async (el) => {
  let lastStatus = null;
  async function refresh(first) {
    let r;
    try { r = await API.get("/api/patient/queue"); }
    catch (e) { if (first) el.innerHTML = errorBox("Queue information is temporarily unavailable."); else $("#live")?.replaceChildren(document.createTextNode("Connection lost — retrying…")); return; }
    const q = r.queue;
    if (!q) { el.innerHTML = `<div class="page-head"><div><h1>My Queue</h1></div></div><div class="card">${empty("You are not in any queue today.")}<div style="text-align:center"><a class="btn" style="color:#fff" href="#/patient/book">Book an appointment</a></div></div>`; return; }
    const e = q.queue_entry;
    if (lastStatus && lastStatus !== e.status) toast(e.status === "CALLED" ? `${e.queue_number}: you have been called!` : `Queue status: ${e.status}`);
    lastStatus = e.status;
    const pct = e.status === "WAITING" ? 100 * q.served_today / (q.served_today + q.patients_ahead + 1) : 100;
    const notJoined = e.status === "WAITING" && !e.joined_at;
    el.innerHTML = `<div class="page-head"><div><h1>My Queue</h1><p>${esc(q.appointment.doctor_name)} · ${esc(q.appointment.department_name)} · ${fmt.date(e.queue_date)}</p></div>
        <small class="muted" id="live">● Live · updated ${fmt.time(q.server_time)}</small></div>
      ${e.status === "CALLED" ? `<div class="alert success"><strong>${esc(e.queue_number)}, it's your turn.</strong> Please proceed to ${esc(q.appointment.doctor_name)}'s consultation room.</div>` : ""}
      ${notJoined ? `<div class="alert warn">You haven't checked in yet. Check in when you arrive at the hospital to join the queue. <button class="btn sm" id="ci" style="margin-left:8px">Check in</button></div>` : ""}
      <div class="hero-q"><div class="card"><div class="card-head"><h3>Your queue number</h3>${fmt.status(e.status)}</div>
          <div class="qnum">${esc(e.queue_number)}</div>
          <div class="grid g3" style="margin:14px 0 10px">
            <div><div class="muted" style="font-size:.8rem">Place in waiting line</div><div style="font-size:1.5rem;font-weight:700">${e.status === "WAITING" ? (e.position ?? "–") : "–"}</div></div>
            <div><div class="muted" style="font-size:.8rem">Patients ahead</div><div style="font-size:1.5rem;font-weight:700">${q.patients_ahead}</div></div>
            <div><div class="muted" style="font-size:.8rem">Now serving</div><div style="font-size:1.5rem;font-weight:700">${esc(q.now_serving || "–")}</div></div></div>
          <div class="progress" aria-label="Queue progress"><div style="width:${Math.min(100, Math.max(3, pct))}%"></div></div>
          <small class="muted">${q.served_today} served today · ${q.queue_length} in queue</small></div>
        ${etaCard(q.prediction)}</div>
      <div class="card"><h3>Live queue board</h3><div class="board">${q.board.map((b) => `<span class="tok ${b.status} ${b.is_me ? "me" : ""}" title="${b.status}">${esc(b.queue_number)}</span>`).join("") || empty("Queue is empty.")}</div>
        <p class="hint" style="margin-top:10px">Blue = in consultation · purple = called · your token is outlined. This page refreshes automatically every 5 seconds.</p></div>
      ${r.other_entries_today?.length ? `<div class="card"><h3>Other appointments today</h3>${r.other_entries_today.map((o) => `<span class="badge" style="margin-right:6px">${esc(o.queue_number)} · ${o.status}</span>`).join("")}</div>` : ""}`;
    if ($("#ci")) $("#ci").onclick = (ev) => busy(ev.currentTarget, "Checking in…", async () => { try { await API.post(`/api/patient/appointments/${q.appointment.id}/check-in`); refresh(); } catch (err) { toast(err.message, "error"); } });
  }
  el.innerHTML = loading("Loading queue…");
  await refresh(true);
  App.every(5000, () => refresh(false));
});

App.route("/patient/appointments", "PATIENT", async (el) => {
  const draw = async () => {
    const r = await API.get("/api/patient/appointments");
    const today = todayISO();
    el.innerHTML = `<div class="page-head"><div><h1>Appointments</h1><p>All your bookings.</p></div><a class="btn" style="color:#fff" href="#/patient/book">＋ Book appointment</a></div>
      <div class="card">${r.appointments.length ? `<div class="table-wrap"><table><tr><th>Date</th><th>Time</th><th>Doctor</th><th>Department</th><th>Queue no.</th><th>Status</th><th></th></tr>
      ${r.appointments.map((a) => `<tr><td>${fmt.date(a.appointment_date)}</td><td>${esc(a.appointment_time)}</td><td>${esc(a.doctor_name)}</td><td>${esc(a.department_name)}</td>
        <td><strong>${esc(a.queue_number || "–")}</strong></td><td>${fmt.status(a.status)}</td>
        <td class="num">${["BOOKED", "CONFIRMED"].includes(a.status) && a.queue_status === "WAITING" && a.appointment_date >= today ? `<button class="btn danger sm" data-cancel="${a.id}">Cancel</button>` : ""}
        ${a.appointment_date === today && ["WAITING", "CALLED", "SERVING"].includes(a.queue_status) ? `<a class="btn secondary sm" href="#/patient/queue">Track</a>` : ""}</td></tr>`).join("")}</table></div>` : empty("You have no appointments yet.")}</div>`;
    $$("[data-cancel]").forEach((b) => b.onclick = () => {
      const m = modal(`<h2>Cancel appointment?</h2><p>Your slot and queue number will be released. This cannot be undone.</p><div class="btn-row"><button class="btn danger" id="yes">Cancel appointment</button><button class="btn secondary" id="no">Keep it</button></div>`);
      $("#no", m.el).onclick = m.close;
      $("#yes", m.el).onclick = (e) => busy(e.currentTarget, "Cancelling…", async () => {
        try { await API.post(`/api/patient/appointments/${b.dataset.cancel}/cancel`); m.close(); toast("Appointment cancelled"); draw(); }
        catch (err) { m.close(); toast(err.message, "error"); }
      });
    });
  };
  await draw();
});

App.route("/patient/departments", "PATIENT", async (el) => {
  const r = await API.get("/api/departments");
  el.innerHTML = `<div class="page-head"><div><h1>Departments</h1><p>Outpatient departments accepting appointments.</p></div></div>
    <div class="grid g3">${r.departments.map((d) => `<div class="card"><div class="card-head"><h3>${esc(d.name)}</h3><span class="badge">${esc(d.prefix)}</span></div>
      <p>${esc(d.description || "")}</p><small class="muted">${d.doctor_count} doctor(s)</small>
      <div style="margin-top:12px"><a class="btn secondary sm" href="#/patient/doctors?d=${d.id}">View doctors</a></div></div>`).join("")}</div>`;
});

App.route("/patient/doctors", "PATIENT", async (el) => {
  const params = new URLSearchParams(location.hash.split("?")[1] || "");
  const [deps, docs] = await Promise.all([API.get("/api/departments"), API.get("/api/doctors")]);
  const draw = (dep) => {
    const list = docs.doctors.filter((d) => !dep || d.department_id == dep);
    $("#dl").innerHTML = list.length ? `<div class="table-wrap"><table><tr><th>Doctor</th><th>Department</th><th>Specialization</th><th>Days</th><th>Hours</th><th></th></tr>
      ${list.map((d) => `<tr><td><strong>${esc(d.name)}</strong></td><td>${esc(d.department_name)}</td><td>${esc(d.specialization || "")}</td>
      <td>${d.working_days.split(",").map((x) => fmt.day(+x)).join(", ")}</td><td>${d.start_time}–${d.end_time}</td><td><a class="btn sm" style="color:#fff" href="#/patient/book?doctor=${d.id}">Book</a></td></tr>`).join("")}</table></div>` : empty("No doctors found.");
  };
  el.innerHTML = `<div class="page-head"><div><h1>Doctors</h1><p>Consulting doctors and their schedules.</p></div>
    <select id="df" style="max-width:260px"><option value="">All departments</option>${deps.departments.map((d) => `<option value="${d.id}" ${params.get("d") == d.id ? "selected" : ""}>${esc(d.name)}</option>`).join("")}</select></div>
    <div class="card" id="dl"></div>`;
  $("#df").onchange = () => draw($("#df").value);
  draw(params.get("d"));
});
App.route("/patient/profile", "PATIENT", profilePage);
