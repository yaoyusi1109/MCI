// AI simulation tab: AI-estimated inputs (low / likely / high) -> one t0 Gurobi plan each.
// A separate model from the scenario simulation: it has its own site, ambulances and hospitals,
// and never reads or writes the scenario CSVs. Backend: /api/ai-sim/* in app.py, which calls
// ../AI_simulation/mci_ai. Ported from AI_simulation/dashboard/demo.html (flood inputs left out
// for now: the water level is sent as 0, so no road counts as flooded).
// Uses the page's shared Leaflet `map`; its layers live in their own panes (aiPlan, aiMarkers),
// which index.html shows only while this tab is open.
(() => {
  const $ = id => document.getElementById("ai-" + id);
  const NAMES = ["low", "likely", "high"];
  const FLOOD_LEVEL_FT = 0;
  const S = { defaults: null, site: null, ambulances: [], hospitalIds: [], raw: null,
              src: "manual defaults", plans: {}, show: "likely", started: false, framed: false };

  map.createPane("aiPlan").style.zIndex = 430;
  map.createPane("aiMarkers").style.zIndex = 640;
  // Added to the map only while the tab is open (tooltips would otherwise stay visible on other tabs).
  const planLayer = L.layerGroup();
  const markerLayer = L.layerGroup();

  const esc = x => String(x).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ---------------- map ----------------
  function dot(color, r = 7) { return { pane: "aiMarkers", radius: r, color: "#fff", weight: 2, fillColor: color, fillOpacity: 1 }; }
  function draggable(latlng, color, title, onMove) {
    const icon = L.divIcon({ className: "", iconSize: [18, 18],
      html: `<div title="${esc(title)}" style="width:18px;height:18px;border-radius:50%;background:${color};border:2px solid #fff;box-shadow:0 0 2px #000"></div>` });
    const m = L.marker(latlng, { icon, draggable: true, pane: "aiMarkers" }).bindTooltip(title);
    m.on("dragend", e => { const p = e.target.getLatLng(); onMove(+p.lat.toFixed(5), +p.lng.toFixed(5)); });
    return m;
  }
  function drawMarkers() {
    markerLayer.clearLayers();
    for (const h of S.defaults.hospitals) {
      const on = S.hospitalIds.includes(h.id);
      L.circleMarker([h.lat, h.lon], dot(on ? (h.trauma ? "#e5484d" : "#f59e0b") : "#9aa5b1", 8))
        .bindTooltip(`${h.id} ${h.name}${h.trauma ? " (trauma)" : ""}${on ? "" : " (click to target)"}`)
        .on("click", () => toggleHospital(h.id)).addTo(markerLayer);
    }
    S.ambulances.forEach((a, i) => draggable([a.lat, a.lon], "#1f6feb", a.id,
      (lat, lon) => { S.ambulances[i].lat = lat; S.ambulances[i].lon = lon; renderAmbTable(); inputsChanged(); }).addTo(markerLayer));
    draggable([S.site.lat, S.site.lon], "#111", "MCI site",
      (lat, lon) => { S.site = { lat, lon }; $("siteLat").value = lat; $("siteLon").value = lon; inputsChanged(); }).addTo(markerLayer);
  }
  function frame() {
    const pts = [[S.site.lat, S.site.lon], ...S.ambulances.map(a => [a.lat, a.lon]),
      ...S.defaults.hospitals.filter(h => S.hospitalIds.includes(h.id)).map(h => [h.lat, h.lon])];
    map.fitBounds(L.latLngBounds(pts), { padding: [40, 40] });
  }

  // ---------------- sub-tabs ----------------
  $("subtabs").onclick = e => {
    const t = e.target.closest("button")?.dataset.sub; if (!t) return;
    for (const b of $("subtabs").children) b.classList.toggle("on", b.dataset.sub === t);
    document.querySelectorAll("#pane-ai .sub").forEach(p => p.classList.toggle("on", p.id === "ai-sub-" + t));
  };

  // ---------------- constants ----------------
  function renderAmbTable() {
    $("ambTable").innerHTML = "<tr><th>Id</th><th>Latitude</th><th>Longitude</th><th></th></tr>" +
      S.ambulances.map((a, i) => `<tr><td>${esc(a.id)}</td>
        <td><input data-i="${i}" data-k="lat" value="${a.lat}" inputmode="decimal"></td>
        <td><input data-i="${i}" data-k="lon" value="${a.lon}" inputmode="decimal"></td>
        <td><button class="button" style="padding:3px 8px" data-del="${i}" aria-label="Remove ${esc(a.id)}">✕</button></td></tr>`).join("");
  }
  $("ambTable").onchange = e => { const { i, k } = e.target.dataset; if (i == null) return;
    S.ambulances[i][k] = +e.target.value; drawMarkers(); inputsChanged(); };
  $("ambTable").onclick = e => { const i = e.target.dataset.del; if (i == null) return;
    if (S.ambulances.length <= 1) return alert("Keep at least one ambulance.");
    S.ambulances.splice(+i, 1); relabelAmb(); renderAmbTable(); drawMarkers(); inputsChanged(); };
  $("addAmb").onclick = () => {
    if (S.ambulances.length >= 8) return alert("At most 8 ambulances (Gurobi restricted license).");
    S.ambulances.push({ id: "", lat: +(S.site.lat + 0.02).toFixed(5), lon: +(S.site.lon - 0.02).toFixed(5) });
    relabelAmb(); renderAmbTable(); drawMarkers(); inputsChanged();
  };
  function relabelAmb() { S.ambulances.forEach((a, i) => a.id = "A" + (i + 1)); }

  function renderHospList() {
    $("hospList").innerHTML = S.defaults.hospitals.map(h => `<label style="display:block;font-size:13px;margin:3px 0">
      <input type="checkbox" data-h="${h.id}" ${S.hospitalIds.includes(h.id) ? "checked" : ""}>
      ${esc(h.id)} ${esc(h.name)} ${h.trauma ? '<span class="tag">trauma</span>' : ""}</label>`).join("");
  }
  $("hospList").onchange = e => toggleHospital(e.target.dataset.h);
  function toggleHospital(id) {
    S.hospitalIds = S.hospitalIds.includes(id) ? S.hospitalIds.filter(x => x !== id) : [...S.hospitalIds, id].sort();
    if (!S.hospitalIds.length) S.hospitalIds = [id];
    renderHospList(); drawMarkers(); ensureRawHospitals(); renderParamTable(); inputsChanged();
  }
  function renderAssumptions() {
    const A = S.defaults.assumptions;
    $("assumptions").innerHTML = Object.keys(A).map(k =>
      `<div><label for="ai-a_${k}">${esc(k)}</label><input id="ai-a_${k}" value="${esc(A[k])}"></div>`).join("");
  }

  // ---------------- casualty parameters (editable AI output) ----------------
  function manualRaw() {
    const sev = { low: 0.6, likely: 1, high: 1.6 }, curve = { low: "optimistic", likely: "moderate", high: "pessimistic" };
    return { assumptions: ["Manual default values; nothing estimated yet."],
      scenarios: NAMES.map(n => ({ name: n, immediate: Math.round(5 * sev[n]), delayed: Math.round(10 * sev[n]),
        minor: Math.round(15 * sev[n]), scene_delay_min: Math.round(6 + 4 * sev[n]), survival_curve: curve[n],
        approach_delay_factor: +(1 + 0.2 * sev[n]).toFixed(2), hospitals: [] })) };
  }
  function hospDefault(id, name) {
    const h = S.defaults.hospitals.find(x => x.id === id), s = { low: 0.6, likely: 1, high: 1.6 }[name];
    return { id, available_beds: Math.max(2, Math.round((h.trauma ? 14 : 10) / s)), route_delay_factor: +(1 + 0.25 * s).toFixed(2) };
  }
  function ensureRawHospitals() {
    for (const sc of S.raw.scenarios)
      for (const id of S.hospitalIds)
        if (!sc.hospitals.find(h => h.id === id)) sc.hospitals.push(hospDefault(id, sc.name));
  }
  const ROWS = [
    ["immediate", "Red (immediate)"], ["delayed", "Yellow (delayed)"], ["minor", "Green (minor, not transported)"],
    ["scene_delay_min", "Scene delay before first load (min)"], ["survival_curve", "Survival curve"],
    ["approach_delay_factor", "Ambulance approach delay ×"],
  ];
  function renderParamTable() {
    const sc = n => S.raw.scenarios.find(s => s.name === n);
    let html = '<table class="tbl"><tr><th></th>' + NAMES.map(n => `<th>${n}</th>`).join("") + "</tr>";
    for (const [k, label] of ROWS) {
      html += `<tr><td>${label}</td>` + NAMES.map(n => {
        const v = sc(n)[k];
        if (k === "survival_curve")
          return `<td><select data-n="${n}" data-k="${k}">${S.defaults.survival_curves.map(c =>
            `<option ${c === v ? "selected" : ""}>${c}</option>`).join("")}</select></td>`;
        return `<td><input data-n="${n}" data-k="${k}" value="${esc(v)}" inputmode="decimal"></td>`;
      }).join("") + "</tr>";
    }
    for (const id of S.hospitalIds) {
      for (const [k, label] of [["available_beds", "beds"], ["route_delay_factor", "route delay ×"]]) {
        html += `<tr><td>${id} ${label}</td>` + NAMES.map(n => {
          const h = sc(n).hospitals.find(x => x.id === id);
          return `<td><input data-n="${n}" data-h="${id}" data-k="${k}" value="${h ? esc(h[k]) : ""}" inputmode="decimal"></td>`;
        }).join("") + "</tr>";
      }
    }
    $("paramTable").innerHTML = html + "</table>";
    $("srcTag").textContent = S.src;
  }
  $("paramTable").onchange = e => {
    const { n, h, k } = e.target.dataset; if (!n) return;
    const sc = S.raw.scenarios.find(s => s.name === n);
    const val = k === "survival_curve" ? e.target.value : +e.target.value;
    if (h) sc.hospitals.find(x => x.id === h)[k] = val; else sc[k] = val;
    if (!S.src.endsWith("edited")) { S.src += " · edited"; $("srcTag").textContent = S.src; }
  };

  // ---------------- inputs for the backend ----------------
  function inputs() {
    const constants = { speed_kmh: +$("c_speed_kmh").value, flood_level_ft: FLOOD_LEVEL_FT, incident: $("incident").value };
    for (const k of Object.keys(S.defaults.assumptions)) {
      const raw = $("a_" + k).value, like = S.defaults.assumptions[k];
      constants[k] = typeof like === "boolean" ? /^(1|true|yes|y)$/i.test(raw) : typeof like === "number" ? +raw : raw;
    }
    return { site: S.site, ambulances: S.ambulances, hospital_ids: S.hospitalIds, constants };
  }
  function inputsChanged() { if (Object.keys(S.plans).length) { S.plans = {}; planLayer.clearLayers(); renderPlan(); } }
  $("siteLat").onchange = $("siteLon").onchange = () => {
    S.site = { lat: +$("siteLat").value, lon: +$("siteLon").value }; drawMarkers(); inputsChanged(); };

  async function api(path, body) {
    const r = await fetch(path, { method: body ? "POST" : "GET", headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : undefined });
    let j = null; try { j = await r.json(); } catch {}
    if (!r.ok) throw new Error((j && (j.detail || j.error)) || `HTTP ${r.status}`);
    return j;
  }

  // ---------------- run log ----------------
  function log(text, cls = "ok") { const li = document.createElement("li"); li.className = cls; li.textContent = text; $("log").appendChild(li); return li; }
  function setBusy(b) { ["aiBtn", "runAll", "solveBtn"].forEach(id => $(id).disabled = b); }

  async function runAI() {
    const backend = $("backend").value;
    const li = log(`AI estimate (${backend})…`, "run");
    try {
      const res = await api("/api/ai-sim/estimate", { ...inputs(), backend, fresh: $("fresh").checked });
      S.raw = res.raw; ensureRawHospitals();
      const m = res.meta;
      S.src = m.backend === "claude" ? `AI: claude ${m.model}${m.cached ? " (cached)" : ""}` : "mock rules";
      li.className = "ok";
      li.textContent = `AI estimate: ${S.src}, ${m.seconds}s; ${res.validation_log.length} value(s) clamped`;
      $("aiDetails").style.display = "";
      $("aiAssumptions").innerHTML = "<ul>" + (res.raw.assumptions || []).map(a => `<li>${esc(a)}</li>`).join("") + "</ul>";
      $("aiValidation").textContent = res.validation_log.join("\n") || "All values accepted.";
      $("aiPrompt").textContent = res.prompt;
      $("aiRaw").textContent = JSON.stringify(res.raw, null, 1);
      renderParamTable();
      return true;
    } catch (e) { li.className = "err"; li.textContent = `AI estimate failed: ${e.message}`; return false; }
  }

  async function solveAll() {
    const inp = inputs();
    let li = log("Travel times to target hospitals…", "run");
    try {
      const f = await api("/api/ai-sim/features", inp);
      li.className = "ok";
      li.textContent = "Travel times: " + f.features.routes.map(r => `${r.hospital} ${r.clear_min} min`).join(", ");
    } catch (e) { li.className = "err"; li.textContent = "Travel times failed: " + e.message; return; }
    S.plans = {};
    for (const n of NAMES) {
      li = log(`Gurobi, ${n} scenario…`, "run");
      try {
        const p = await api("/api/ai-sim/solve", { ...inp, raw: S.raw, name: n });
        S.plans[n] = p;
        li.className = p.trips ? "ok" : "err";
        li.textContent = `Gurobi (${n}): ${p.status_name} in ${p.seconds}s` +
          (p.trips ? `, ${p.transported} transported, expected survivors ${p.expected_survivors}` : "");
        if (n === S.show) renderPlan();
      } catch (e) { li.className = "err"; li.textContent = `Gurobi (${n}) failed: ${e.message}`; }
    }
    renderPlan();
  }

  function toRun() { $("subtabs").querySelector('[data-sub="run"]').click(); }
  $("aiBtn").onclick = async () => { setBusy(true); $("log").innerHTML = ""; await runAI(); setBusy(false); };
  $("solveBtn").onclick = async () => { setBusy(true); $("log").innerHTML = ""; await solveAll(); setBusy(false); };
  $("runAll").onclick = async () => { setBusy(true); $("log").innerHTML = ""; toRun();
    if (await runAI()) await solveAll(); setBusy(false); };
  $("planSeg").onclick = e => { const p = e.target.closest("button")?.dataset.p; if (!p) return; S.show = p;
    [...$("planSeg").children].forEach(b => b.classList.toggle("on", b.dataset.p === p)); renderPlan(); };

  // ---------------- results ----------------
  function renderPlan() {
    planLayer.clearLayers();
    const p = S.plans[S.show];
    if (!p) { $("planOut").innerHTML = '<p class="small">No plan yet.</p>'; return; }
    if (!p.trips) { $("planOut").innerHTML = `<p class="small">${esc(p.status_name)}: no plan found.</p>`; return; }
    const H = Object.fromEntries(S.defaults.hospitals.map(h => [h.id, h]));
    const first = {};
    for (const t of p.trips) if (!first[t.ambulance] || t.trip < first[t.ambulance].trip) first[t.ambulance] = t;
    for (const a of S.ambulances) if (first[a.id])
      L.polyline([[a.lat, a.lon], [S.site.lat, S.site.lon]], { pane: "aiPlan", color: "#1f6feb", weight: 2, dashArray: "5 5" }).addTo(planLayer);
    const flow = {};
    for (const t of p.trips) { const k = t.hospital; flow[k] = flow[k] || { immediate: 0, delayed: 0 }; flow[k][t.class]++; }
    for (const [hid, f] of Object.entries(flow)) {
      const h = H[hid], n = f.immediate + f.delayed;
      L.polyline([[S.site.lat, S.site.lon], [h.lat, h.lon]], { pane: "aiPlan", color: f.immediate ? "#cf222e" : "#d4a72c", weight: 2 + 2 * n, opacity: 0.75 })
        .bindTooltip(`${hid}: ${f.immediate} red, ${f.delayed} yellow`, { permanent: true, direction: "center" }).addTo(planLayer);
    }
    const unserved = p.unserved.immediate + p.unserved.delayed;
    const ma = p.mean_arrival;
    let html = `<table class="tbl stats">
      <tr><td>Solver</td><td>${esc(p.status_name)}, ${p.seconds}s, gap ${(100 * (p.mip_gap || 0)).toFixed(2)}%</td></tr>
      <tr><td>Model size</td><td>${p.size.vars} vars (${p.size.bin_int_vars} integer), ${p.size.linear_constrs} constraints</td></tr>
      <tr><td>Objective</td><td>${p.objective === "flow_time" ? "min total time to hospital" : "max expected survival"}</td></tr>
      <tr><td>Transported</td><td>${p.transported}${unserved ? ` (unserved: ${p.unserved.immediate} red, ${p.unserved.delayed} yellow)` : ""}</td></tr>
      <tr><td>Expected survivors (Chang curves)</td><td>${p.expected_survivors}</td></tr>
      <tr><td>Mean arrival, red / yellow (min)</td><td>${ma.immediate ?? "–"} / ${ma.delayed ?? "–"}</td></tr>
      <tr><td>Last hospital arrival (min)</td><td>${p.makespan}</td></tr></table>`;
    html += '<h2 style="margin-top:12px">Beds (after green walk-ins)</h2><table class="tbl"><tr><th>Hospital</th><th>Input</th><th>Walk-ins</th><th>Usable</th><th>Used</th><th>Travel min</th></tr>' +
      Object.entries(p.beds).map(([h, b]) => `<tr><td>${h}</td><td>${b.reported}</td><td>${b.walk_ins}</td><td>${b.usable}</td><td>${b.used}</td><td>${p.travel_min[h]}</td></tr>`).join("") + "</table>";
    html += '<h2 style="margin-top:12px">Trips</h2><table class="tbl"><tr><th>Amb</th><th>#</th><th>Patient</th><th>Pickup</th><th>Hospital</th><th>Arrive</th><th>P(survive)</th></tr>' +
      p.trips.map(t => `<tr><td>${t.ambulance}</td><td>${t.trip}</td><td>${t.patient}</td><td>${t.pickup}</td><td>${t.hospital}</td><td>${t.hospital_arrival}</td><td>${t.survival.toFixed(2)}</td></tr>`).join("") + "</table>";
    if (p.validation_log && p.validation_log.length) html += `<details><summary>Input values clamped before solving</summary><pre>${esc(p.validation_log.join("\n"))}</pre></details>`;
    $("planOut").innerHTML = html;
  }

  // ---------------- start (lazily, the first time the tab opens) ----------------
  async function start() {
    try { S.defaults = await api("/api/ai-sim/defaults"); }
    catch (e) { $("backendNote").textContent = `AI simulation backend unavailable: ${e.message}`; S.started = false; return; }
    const D = S.defaults;
    S.site = { ...D.site }; S.ambulances = D.ambulances.map(a => ({ ...a })); S.hospitalIds = [...D.hospital_ids];
    $("siteLat").value = S.site.lat; $("siteLon").value = S.site.lon;
    $("incident").value = D.constants.incident; $("c_speed_kmh").value = D.constants.speed_kmh;
    const b = D.backends;
    $("backendNote").textContent = b.claude_cli
      ? "Uses the Claude Code login (or ANTHROPIC_API_KEY) of whoever runs app.py. Nothing is stored in this project. Responses are cached; tick 'fresh call' to re-ask."
      : "No `claude` CLI found on this machine, so the AI step will fail; use Mock, or install Claude Code.";
    if (!b.claude_cli) $("backend").value = "mock";
    S.raw = manualRaw(); ensureRawHospitals();
    renderAmbTable(); renderHospList(); renderAssumptions(); renderParamTable(); drawMarkers();
  }
  window.aiSim = {
    async activate() {
      planLayer.addTo(map); markerLayer.addTo(map);
      if (!S.started) { S.started = true; await start(); }
      if (S.defaults && !S.framed) { S.framed = true; frame(); }
    },
    deactivate() { map.removeLayer(planLayer); map.removeLayer(markerLayer); },
  };
  if (typeof activeTab !== "undefined" && activeTab === "ai") window.aiSim.activate();
})();
