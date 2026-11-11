// Simulation tab: "Why is the ambulance doing this?" and "What if a road is blocked?".
// The explanation is read from the saved run (no solver calls during playback):
//   optimizer_decisions[].explanation  - why this patient / this hospital (gurobi_dispatch._explain)
//   routes[].why                       - why this route (engine.get_route explain=True)
//   routes[].rerouted, events          - road closures during the run and how the ambulance reacted
// Road blocks are sent with the next run as `road_blocks` (app.py); the engine adds each one at its
// minute and re-routes a moving ambulance whose remaining path crosses it.
(() => {
  const $ = id => document.getElementById(id);
  const esc = x => String(x ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 1) => (v == null || !Number.isFinite(Number(v)) ? "–" : Number(v).toFixed(d));
  const signed = v => (Number(v) >= 0 ? "+" : "") + fmt(v, 1);
  const ROAD_EVENTS = new Set(["ROAD_CLOSED", "AMBULANCE_REROUTED", "TRAVEL_TIMES_UPDATED", "REROUTE_FAILED"]);
  const S = { blocks: [], picking: false, lastKey: null, sent: "[]", listFor: null, amb: null };
  // Which ambulance a route belongs to (older static runs carry no id: they have one ambulance).
  const ambOf = r => r.ambulance_id || simData?.assignments?.[0]?.ambulance_id || "A001";

  map.createPane("simBlocks").style.zIndex = 660;
  const blockLayer = L.layerGroup().addTo(map);

  // ---------------- helpers over the saved run ----------------
  function hospitalName(id) {
    const row = (activeScenario?.tables?.["hospitals.csv"]?.rows || []).find(r => r.hospital_id === id);
    return row ? `${id} ${row.name}` : String(id ?? "?");
  }
  const sortedRoutes = () => (simData?.routes || [])
    .filter(r => ambOf(r) === S.amb && Number.isFinite(Number(r.start_min)) && Number.isFinite(Number(r.end_min)))
    .slice().sort((a, b) => Number(a.start_min) - Number(b.start_min));
  const myDecisions = () => (simData?.optimizer_decisions || []).filter(d => (d.ambulance_id || S.amb) === S.amb);
  function decisionFor(pid, t) {
    return myDecisions().filter(d => d.selected_patient_id === pid && Number(d.decision_time_min) <= t + 1e-6).at(-1);
  }

  // What the ambulance is doing at t, and which route/decision explains it.
  function stateAt(t) {
    const routes = sortedRoutes();
    let cur = null;
    for (const r of routes) if (Number(r.start_min) <= t && t <= Number(r.end_min)) cur = r; // later (re-routed) wins
    const prev = routes.filter(r => Number(r.end_min) < t).at(-1);
    if (cur) return { kind: cur.purpose, route: cur };
    if (!prev) return { kind: "WAITING" };
    if (prev.purpose === "MCI_TO_HOSPITAL") return { kind: "HANDOVER", route: prev };
    const next = routes.find(r => Number(r.start_min) > t && r.purpose === "MCI_TO_HOSPITAL");
    const d = myDecisions().filter(x => Number(x.decision_time_min) <= t + 1e-6).at(-1);
    if (next && d && d.selected_patient_id === next.patient_id) return { kind: "LOADING", next, decision: d };
    return { kind: "AT_CCP" };
  }

  // ---------------- explanation blocks ----------------
  function patientTable(x) {
    return `<h3>Why this patient</h3><table class="tbl"><tr><th>Patient</th><th>Triage</th><th>Waited</th><th>Reason</th></tr>` +
      x.patients.map(p => `<tr class="${p.chosen ? "chosen" : ""}"><td>${esc(p.id)}</td><td><span class="tri ${esc(p.observed)}">${esc(p.observed)}</span></td>` +
        `<td>${fmt(p.waiting_min)} min</td><td>${esc(p.why)}</td></tr>`).join("") + "</table>";
  }
  function hospitalTable(x) {
    return `<h3>Why this hospital</h3><table class="tbl"><tr><th>Hospital</th><th>Beds</th><th>Drive</th><th>Reason</th></tr>` +
      x.hospitals.map(h => `<tr class="${h.chosen ? "chosen" : ""}"><td>${esc(hospitalName(h.id))}</td><td>${esc(h.beds)}</td>` +
        `<td>${h.travel_min == null ? "–" : fmt(h.travel_min) + " min"}</td><td>${esc(h.why)}</td></tr>`).join("") + "</table>";
  }
  function routeBlock(r) {
    const w = r?.why;
    if (!w) return "";
    let html = `<h3>Why this route</h3><p>${esc(w.rule[0].toUpperCase() + w.rule.slice(1))}: ${fmt(r.travel_minutes)} min, ${fmt(r.distance_miles, 2)} mi` +
      (w.via?.length ? `, mainly via ${esc(w.via.join(", "))}` : "") + ".</p>";
    if (w.alternative) {
      const via = w.alternative.via?.length ? " via " + esc(w.alternative.via.join(", ")) : "";
      html += w.alternative.extra_min >= 0
        ? `<p>Next-best route${via}: ${fmt(w.alternative.minutes)} min (${signed(w.alternative.extra_min)} min).</p>`
        : `<p>Next-best route${via}: ${fmt(w.alternative.minutes)} min, ${fmt(-w.alternative.extra_min)} min quicker on paper, ` +
          `but it costs more once its extra turns, signals or minor roads are counted.</p>`;
    } else {
      html += "<p>Valhalla found no meaningfully different alternative.</p>";
    }
    if (w.avoided_closures?.length) {
      html += `<p>Avoids closed road${w.avoided_closures.length > 1 ? "s" : ""}: <b>${esc(w.avoided_closures.join(", "))}</b>. ` +
        `Without ${w.avoided_closures.length > 1 ? "them" : "it"} the trip would take ${fmt(w.unobstructed_minutes)} min (${signed(r.travel_minutes - w.unobstructed_minutes)} min now).</p>`;
    }
    if (r.rerouted) {
      html = `<div class="callout"><b>Re-routed at ${esc(simTime(r.start_min))}</b>: ${esc(r.reroute_reason)} closed ahead on the planned route. ` +
        `The ambulance switched to the best open route from where it was; arrival ${signed(r.delay_min)} min versus the original plan.</div>` + html;
    }
    return html;
  }
  function roadEvents(t) {
    const list = (simData?.events || []).filter(e => ROAD_EVENTS.has(e.event) && Number(e.time_min) <= t + 1e-6);
    if (!list.length) return "";
    const text = e => ({
      ROAD_CLOSED: `Road closed: ${e.road}`,
      AMBULANCE_REROUTED: `${e.ambulance_id} re-routed around ${e.road} (${signed(e.delay_min)} min${e.patient_id ? ", carrying " + e.patient_id : ""})`,
      TRAVEL_TIMES_UPDATED: `Travel times updated for later trips: ${e.hospitals}`,
      REROUTE_FAILED: `${e.ambulance_id} could not re-route around ${e.road}; kept its route`,
    }[e.event]);
    return `<h3>Road events so far</h3><ul>${list.map(e => `<li>${esc(simTime(e.time_min))} · ${esc(text(e))}</li>`).join("")}</ul>`;
  }

  // Fire engines on scene at t: who is there to triage and stabilise, and who holds command.
  function sceneResources(t) {
    const units = simData?.response_units || [];
    if (!units.length) return "";
    const on = units.filter(u => Number(u.arrival_min) <= t);
    const cmd = units.find(u => u.incident_command);
    const next = units.filter(u => Number(u.arrival_min) > t).sort((a, b) => a.arrival_min - b.arrival_min)[0];
    return `<p class="small" style="margin:8px 0 0">On scene: <b>${on.length} of ${units.length}</b> fire engines ` +
      `(${on.reduce((n, u) => n + (u.crew || 0), 0)} firefighters triaging and stabilising)` +
      (next ? `; next ${esc(next.unit_id)} arrives ${esc(simTime(next.arrival_min))}` : "") +
      (cmd && Number(cmd.arrival_min) <= t
        ? `. Incident command: ${esc(cmd.unit_id)} (${esc(cmd.station || "")}), so the ambulance crew stays free to transport.`
        : ". Incident command starts when the first fire engine arrives.") + "</p>";
  }

  function render(t) {
    const box = $("simWhy");
    if (!simData) { box.textContent = "Run a simulation, then play or drag the timeline."; S.lastKey = null; return; }
    if (S.listFor !== simData) { S.listFor = simData; S.lastKey = null; renderList(); fillAmbulances(); } // a new run arrived
    drawBlocks(t);
    if (simData.mode !== "dynamic") {
      box.innerHTML = "<p>Step-by-step reasons are recorded by the <b>Dynamic Gurobi</b> mode. Switch the mode above and run again.</p>";
      return;
    }
    const st = stateAt(t);
    const nEvents = (simData.events || []).filter(e => ROAD_EVENTS.has(e.event) && Number(e.time_min) <= t + 1e-6).length;
    const nOnScene = (simData.response_units || []).filter(u => Number(u.arrival_min) <= t).length;
    const key = [S.amb, st.kind, st.route?.start_min, st.route?.patient_id, st.next?.patient_id, nEvents, nOnScene].join("|");
    if (key === S.lastKey) return; // only rebuild when the state changes, not on every animation frame
    S.lastKey = key;

    let now = "", body = "";
    const r = st.route;
    if (st.kind === "WAITING") now = "Not dispatched yet.";
    else if (st.kind === "STATION_TO_MCI") { now = "Driving from the station to the CCP (collection point)."; body = routeBlock(r); }
    else if (st.kind === "MCI_TO_HOSPITAL" || st.kind === "LOADING") {
      const pid = st.kind === "LOADING" ? st.next.patient_id : r.patient_id;
      const hid = st.kind === "LOADING" ? st.next.hospital_id : r.hospital_id;
      const d = st.decision || decisionFor(pid, t);
      const tri = d?.selected_priority ? ` (${d.selected_priority} when chosen)` : "";
      now = st.kind === "LOADING"
        ? `Loading <b>${esc(pid)}</b>${esc(tri)} for <b>${esc(hospitalName(hid))}</b>.`
        : `Taking <b>${esc(pid)}</b>${esc(tri)} to <b>${esc(hospitalName(hid))}</b>.`;
      const x = d?.explanation;
      body = (x ? `<p class="rule">Gurobi picked this at ${esc(simTime(d.decision_time_min))}. Rule, in order: ${esc(x.rule)}.</p>` +
        patientTable(x) + hospitalTable(x) : "<p>This run has no recorded explanation (run again to record one).</p>") +
        (st.kind === "MCI_TO_HOSPITAL" ? routeBlock(r) : "");
    } else if (st.kind === "HANDOVER") now = `Handing over <b>${esc(r.patient_id)}</b> at <b>${esc(hospitalName(r.hospital_id))}</b> (hospital turnover from the scenario config).`;
    else if (st.kind === "HOSPITAL_TO_MCI") { now = `Returning from <b>${esc(hospitalName(r.hospital_id))}</b> to the CCP for the next patient.`; body = routeBlock(r); }
    else now = "At the CCP, waiting for the next triaged patient.";
    box.innerHTML = `<div class="now"><b>${esc(S.amb)}</b>: ${now}</div>${sceneResources(t)}${body}${roadEvents(t)}`;
  }

  // ---------------- which ambulance to explain ----------------
  function fillAmbulances() {
    const ids = [...new Set((simData?.routes || []).map(ambOf))].sort();
    const sel = $("whyAmb");
    sel.innerHTML = ids.map(id => `<option value="${esc(id)}">${esc(id)}</option>`).join("");
    sel.disabled = ids.length < 2;
    if (!ids.includes(S.amb)) {
      // Default: the first ambulance that carried a patient, else the first one.
      const first = (simData?.routes || []).filter(r => r.purpose === "MCI_TO_HOSPITAL")
        .sort((a, b) => a.start_min - b.start_min)[0];
      S.amb = first ? ambOf(first) : ids[0] ?? null;
    }
    sel.value = S.amb ?? "";
  }
  function selectAmbulance(aid) {
    S.amb = aid; $("whyAmb").value = aid; S.lastKey = null;
    if (typeof simMinute === "number") renderSim(simMinute); // redraw markers (selection ring) and panel
  }
  $("whyAmb").onchange = e => selectAmbulance(e.target.value);

  // ---------------- what-if road blocks ----------------
  function drawBlocks(t) {
    blockLayer.clearLayers();
    S.blocks.forEach((b, i) => {
      const pending = simData && Number.isFinite(t) && t < b.at_min;
      const icon = L.divIcon({ className: "", iconSize: [16, 16], iconAnchor: [8, 8], html: `<div class="rb-pin ${pending ? "pending" : ""}"></div>` });
      L.marker([b.lat, b.lon], { icon, pane: "simBlocks", keyboard: false })
        .bindTooltip(`Road block ${i + 1} · from minute ${b.at_min}${pending ? " (not yet)" : ""}`).addTo(blockLayer);
    });
  }
  function renderList() {
    $("rbList").innerHTML = S.blocks.map((b, i) =>
      `<li><span>Road block ${i + 1} · from minute ${esc(b.at_min)} · ${b.lat.toFixed(4)}, ${b.lon.toFixed(4)}</span>` +
      `<button data-i="${i}" title="Remove" aria-label="Remove road block ${i + 1}">×</button></li>`).join("") +
      (simData && JSON.stringify(S.blocks) !== S.sent ? `<li class="small" style="border:0">Run the simulation again to apply.</li>` : "");
    drawBlocks(typeof simMinute === "number" ? simMinute : NaN);
  }
  function setPicking(on) {
    S.picking = on;
    map.getContainer().classList.toggle("rb-picking", on);
    $("rbAdd").textContent = on ? "Click a road on the map… (Esc to cancel)" : "Add road block";
  }
  $("rbAdd").onclick = () => setPicking(!S.picking);
  $("rbNow").onclick = () => { $("rbMinute").value = Math.round(typeof simMinute === "number" ? simMinute : 0); };
  $("rbList").onclick = e => {
    const i = e.target.closest("button")?.dataset.i;
    if (i == null) return;
    S.blocks.splice(+i, 1); renderList();
  };
  document.addEventListener("keydown", e => { if (e.key === "Escape" && S.picking) setPicking(false); });

  window.simExplain = {
    render,
    selectAmbulance,
    selectedAmbulance: () => S.amb,
    takeMapClick(latlng) {
      if (!S.picking) return false;
      const at = Math.max(0, Math.min(240, Number($("rbMinute").value) || 0));
      S.blocks.push({ lat: +latlng.lat.toFixed(6), lon: +latlng.lng.toFixed(6), at_min: at });
      setPicking(false); renderList();
      return true;
    },
    requestParams() {
      S.sent = JSON.stringify(S.blocks);
      return S.blocks.length ? { road_blocks: S.blocks.map((b, i) => ({ ...b, label: `Road block ${i + 1}` })) } : {};
    },
  };
})();
