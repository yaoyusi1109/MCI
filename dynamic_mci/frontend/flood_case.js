// Flood tab: the real August 12, 2014 Baltimore flash flood.
// Data: flood_2014.json, built by data/flood_2014/build_flood_case.py (NWS reports, BWI rainfall,
// aggregate Twitter mobility), and /api/flood-2014/closures (closures placed on the roads they name).
// "Run the simulation inside this flood" sends flood_at (the MCI start time) with every route and
// simulation request; the backend then avoids the closures reported by that time. The same switch
// keeps the flood layers on the map in the other tabs.
(() => {
  const $ = id => document.getElementById(id);
  const esc = x => String(x).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const NS = "http://www.w3.org/2000/svg";
  const S = { data: null, closures: [], loading: null, active: false, framed: false, markers: [], apply: false, at: "14:30" };
  try { S.apply = localStorage.getItem("mci.floodApply") === "1"; S.at = localStorage.getItem("mci.floodAt") || "14:30"; } catch {}

  map.createPane("floodReports").style.zIndex = 650;
  map.createPane("floodActivity").style.zIndex = 390; // above tiles, below routes and markers
  const layer = L.layerGroup();
  const heatLayer = L.layerGroup();
  S.heatMode = "all";
  try { S.heatMode = localStorage.getItem("mci.floodHeat") || "all"; } catch {}

  // ---------------- formatting ----------------
  const hourLabel = h => (h % 12 || 12) + (h < 12 ? "a" : "p");
  const clock = hhmm => { const [h, m] = hhmm.split(":").map(Number); return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`; };
  const shortDate = iso => { const [, m, d] = iso.split("-").map(Number); return `${["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][m]} ${d}`; };

  // ---------------- tiny SVG chart kit (one y-axis per chart, hover tooltips) ----------------
  const W = 360, PAD = { l: 34, r: 12, t: 10, b: 22 };
  function svg(h) {
    const s = document.createElementNS(NS, "svg");
    s.setAttribute("viewBox", `0 0 ${W} ${h}`); s.setAttribute("role", "img");
    return s;
  }
  function el(parent, tag, attrs, text) {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    if (text != null) e.textContent = text;
    parent.appendChild(e); return e;
  }
  function niceMax(v) { const p = 10 ** Math.floor(Math.log10(v || 1)); return Math.ceil(v / p) * p; }
  function yAxis(s, h, max, fmt, ticks = 3) {
    for (let i = 0; i <= ticks; i++) {
      const v = max * i / ticks, y = h - PAD.b - (h - PAD.t - PAD.b) * i / ticks;
      el(s, "line", { x1: PAD.l, x2: W - PAD.r, y1: y, y2: y, class: "grid" });
      el(s, "text", { x: PAD.l - 5, y: y + 3, "text-anchor": "end" }, fmt(v));
    }
  }
  function tooltip(box) {
    let tip = box.querySelector(".fl-tip");
    if (!tip) { tip = document.createElement("div"); tip.className = "fl-tip"; box.appendChild(tip); }
    return {
      show(html, xFrac, yFrac) {
        if (!tip.isConnected) box.appendChild(tip); // the chart may have replaced the box's children
        tip.innerHTML = html; tip.style.display = "block";
        const bw = box.clientWidth, tw = tip.offsetWidth;
        tip.style.left = Math.min(Math.max(0, xFrac * bw - tw / 2), bw - tw) + "px";
        tip.style.top = Math.max(0, yFrac * box.clientHeight - tip.offsetHeight - 10) + "px";
      },
      hide() { tip.style.display = "none"; },
    };
  }
  // Transparent hit columns, wider than the marks, drive the tooltip and a crosshair.
  function hover(box, s, h, n, xOf, html, yOf) {
    const tip = tooltip(box), step = (W - PAD.l - PAD.r) / n;
    const cross = el(s, "line", { y1: PAD.t, y2: h - PAD.b, class: "grid", "stroke-dasharray": "3 3", visibility: "hidden" });
    for (let i = 0; i < n; i++) {
      const r = el(s, "rect", { x: xOf(i) - step / 2, y: 0, width: step, height: h, fill: "transparent" });
      r.addEventListener("mouseenter", () => {
        cross.setAttribute("x1", xOf(i)); cross.setAttribute("x2", xOf(i)); cross.setAttribute("visibility", "visible");
        tip.show(html(i), xOf(i) / W, yOf(i) / h);
      });
      r.addEventListener("mouseleave", () => { cross.setAttribute("visibility", "hidden"); tip.hide(); });
    }
  }

  function rainChart(rows) {
    const box = $("fl-rain"), h = 130, s = svg(h), max = niceMax(Math.max(...rows.map(r => r.inches)));
    s.setAttribute("aria-label", "Hourly rainfall at BWI on August 12, 2014");
    const plotH = h - PAD.t - PAD.b, step = (W - PAD.l - PAD.r) / 24, bw = step - 2;
    const xOf = i => PAD.l + step * (i + 0.5), yOf = v => h - PAD.b - plotH * v / max;
    yAxis(s, h, max, v => v ? v.toFixed(1) + '"' : "0");
    rows.forEach((r, i) => {
      if (r.inches <= 0) return;
      const x = xOf(i) - bw / 2, y = yOf(r.inches), rad = Math.min(4, bw / 2, h - PAD.b - y);
      // Rounded data-end, square baseline.
      el(s, "path", { fill: "var(--viz-rain)", d: `M${x},${h - PAD.b}V${y + rad}Q${x},${y} ${x + rad},${y}H${x + bw - rad}Q${x + bw},${y} ${x + bw},${y + rad}V${h - PAD.b}Z` });
    });
    for (const hr of [0, 6, 12, 18]) el(s, "text", { x: xOf(hr), y: h - 6, "text-anchor": "middle" }, hourLabel(hr));
    const peak = rows.reduce((a, b) => (b.inches > a.inches ? b : a));
    el(s, "text", { x: xOf(rows.indexOf(peak)) + bw, y: yOf(peak.inches) + 9, class: "lab" }, `${peak.inches.toFixed(2)}" in one hour`);
    hover(box, s, h, 24, xOf,
      i => `<b>${rows[i].window}</b>${rows[i].trace ? "trace" : rows[i].inches.toFixed(2) + " in"}`,
      i => yOf(rows[i].inches));
    box.replaceChildren(s);
  }

  function activityChart(rows, meta) {
    const box = $("fl-activity"), h = 150, s = svg(h);
    s.setAttribute("aria-label", "Active geotagged users per hour, August 12 versus typical Tuesday");
    const max = niceMax(Math.max(...rows.map(r => Math.max(r.event_users, r.typical_max))));
    const plotH = h - PAD.t - PAD.b, step = (W - PAD.l - PAD.r) / 24;
    const xOf = i => PAD.l + step * (i + 0.5), yOf = v => h - PAD.b - plotH * v / max;
    // Heavy-rain hours from the chart above, as a recessive band.
    el(s, "rect", { x: xOf(11) - step / 2, y: PAD.t, width: step * 5, height: plotH, fill: "var(--soft)" });
    el(s, "text", { x: xOf(13), y: PAD.t + 10, "text-anchor": "middle" }, "heavy rain");
    yAxis(s, h, max, v => String(Math.round(v)));
    const band = rows.map((r, i) => `${xOf(i)},${yOf(r.typical_max)}`).join(" ") + " " +
      rows.map((r, i) => `${xOf(i)},${yOf(r.typical_min)}`).reverse().join(" ");
    el(s, "polygon", { points: band, fill: "var(--muted)", opacity: 0.14 });
    const line = (key, attrs) => el(s, "polyline", { fill: "none", "stroke-width": 2, "stroke-linejoin": "round",
      points: rows.map((r, i) => `${xOf(i)},${yOf(r[key])}`).join(" "), ...attrs });
    line("typical_mean", { stroke: "var(--muted)", "stroke-dasharray": "4 3" });
    line("event_users", { stroke: "var(--viz-mob)" });
    for (const hr of [0, 6, 12, 18]) el(s, "text", { x: xOf(hr), y: h - 6, "text-anchor": "middle" }, hourLabel(hr));
    // Direct labels at the line ends (identity is never colour alone).
    const last = rows[rows.length - 1];
    const yE = yOf(last.event_users), yT = yOf(last.typical_mean), apart = Math.abs(yE - yT) < 11 ? (yE < yT ? -6 : 6) : 0;
    el(s, "text", { x: W - PAD.r - 2, y: yE - 6 + apart, "text-anchor": "end", class: "lab" }, "Aug 12");
    el(s, "text", { x: W - PAD.r - 2, y: yT + 12 - apart, "text-anchor": "end" }, "typical Tue");
    hover(box, s, h, 24, xOf,
      i => `<b>${hourLabel(i)} – ${hourLabel((i + 1) % 24)}</b>Aug 12: ${rows[i].event_users} users<br>Typical Tue: ${rows[i].typical_mean} (range ${rows[i].typical_min}–${rows[i].typical_max})`,
      i => yOf(Math.max(rows[i].event_users, rows[i].typical_max)));
    box.replaceChildren(s);
    const pct = Math.round((meta.storm_users_vs_typical - 1) * 100);
    $("fl-activity-note").innerHTML = `Orange: Aug 12. Dashed line and grey band: mean and range of the other Tuesdays (${meta.compare_days.map(shortDate).join(", ")}). ` +
      `From 11 AM to 4 PM, <b>${pct >= 0 ? "+" : ""}${pct}%</b> more users posted than on a typical Tuesday. This counts posting, not travel.`;
  }

  function dailyChart(rows) {
    const box = $("fl-daily"), h = 130, s = svg(h);
    s.setAttribute("aria-label", "Mean daily activity radius, July 28 to August 27, 2014");
    const vals = rows.map(r => r.mean_rg_km), max = niceMax(Math.max(...vals) * 1.15);
    const plotH = h - PAD.t - PAD.b, step = (W - PAD.l - PAD.r) / rows.length;
    const xOf = i => PAD.l + step * (i + 0.5), yOf = v => h - PAD.b - plotH * v / max;
    yAxis(s, h, max, v => v.toFixed(1) + " km");
    el(s, "polyline", { fill: "none", stroke: "var(--viz-mob)", "stroke-width": 2, "stroke-linejoin": "round",
      points: rows.map((r, i) => `${xOf(i)},${yOf(r.mean_rg_km)}`).join(" ") });
    const ev = rows.findIndex(r => r.date === "2014-08-12");
    if (ev >= 0) {
      el(s, "circle", { cx: xOf(ev), cy: yOf(vals[ev]), r: 4.5, fill: "var(--viz-mob)", stroke: "var(--panel)", "stroke-width": 2 });
      el(s, "text", { x: xOf(ev), y: yOf(vals[ev]) + 16, "text-anchor": "middle", class: "lab" }, "Aug 12");
    }
    rows.forEach((r, i) => { if (i % 7 === 0) el(s, "text", { x: xOf(i), y: h - 6, "text-anchor": "middle" }, shortDate(r.date)); });
    hover(box, s, h, rows.length, xOf,
      i => `<b>${shortDate(rows[i].date)}</b>${rows[i].mean_rg_km.toFixed(2)} km mean radius<br>${rows[i].active_users} active users`,
      i => yOf(vals[i]));
    box.replaceChildren(s);
  }

  // ---------------- stats, reports, map ----------------
  function stats(meta) {
    const pct = Math.round((meta.storm_users_vs_typical - 1) * 100);
    const tiles = [
      [meta.bwi_total_in.toFixed(2) + '"', "Rain at BWI that day"],
      [meta.peak_hour.inches.toFixed(2) + '"', `Peak hour, ${clock(meta.peak_hour.window.split("–")[0])}–${clock(meta.peak_hour.window.split("–")[1])}`],
      [String(meta.road_closures), "Road closures reported (NWS)"],
      [(pct >= 0 ? "+" : "") + pct + "%", "Users posting 11 AM–4 PM vs typical Tue"],
    ];
    $("fl-stats").innerHTML = tiles.map(([v, l]) => `<div class="metric"><strong>${esc(v)}</strong><span>${esc(l)}</span></div>`).join("");
  }

  function popup(r) {
    return `<b>${esc(r.place)}</b> · ${esc(clock(r.time_local.slice(11)))}<br>` +
      `${r.road_closed ? '<span class="tag" style="background:var(--viz-closed);color:#fff">Road closed</span> ' : ""}${esc(r.type)}` +
      `<p style="margin:6px 0 0;max-width:240px">${esc(r.remark)}</p><span class="small">${esc(r.county)} · source: ${esc(r.source)}</span>`;
  }

  // A closure is drawn where it was placed on the named road (the point routing excludes), not at the
  // NWS location, which is rounded to ~1 km.
  const closureFor = r => S.closures.find(c => c.time_local === r.time_local);
  const laterThanStart = r => S.apply && r.time_local.slice(11) > S.at;
  function reports(rows) {
    layer.clearLayers();
    S.markers = rows.map(r => {
      const c = r.road_closed ? closureFor(r) : null;
      const where = c ? [c.exclude_locations[0].lat, c.exclude_locations[0].lon] : [r.lat, r.lon];
      const later = r.road_closed && laterThanStart(r);
      const icon = L.divIcon({ className: "", iconSize: [16, 16], iconAnchor: [8, 8],
        html: `<div class="fl-pin ${r.road_closed ? "closed" : ""} ${later ? "later" : ""}" style="background:var(${r.road_closed ? "--viz-closed" : "--viz-flood"})"></div>` });
      const extra = c ? `<br><span class="small">Routing blocks: ${esc(c.road)} (${esc(c.matched_name)})${later ? " · <b>not yet closed at " + esc(clock(S.at)) + "</b>" : ""}</span>` : "";
      return L.marker(where, { icon, pane: "floodReports", title: r.place, keyboard: false })
        .bindPopup(popup(r) + extra).addTo(layer);
    });
    $("fl-reports").innerHTML = rows.map((r, i) =>
      `<li tabindex="0" data-i="${i}"><span class="t">${esc(clock(r.time_local.slice(11)).replace(" ", " "))}</span>` +
      `<i class="fl-key ${r.road_closed ? "closed" : "flood"}"></i>` +
      `<span><span class="p">${esc(r.place)}</span>${r.road_closed ? " · road closed" : ""}<br><span class="r">${esc(r.remark)}</span></span></li>`).join("");
    const open = li => {
      if (!li) return;
      const m = S.markers[+li.dataset.i];
      map.flyTo(m.getLatLng(), Math.max(map.getZoom(), 14), { duration: 0.6 });
      map.once("moveend", () => m.openPopup());
    };
    $("fl-reports").onclick = e => open(e.target.closest("li"));
    $("fl-reports").onkeydown = e => { if (e.key === "Enter") open(e.target.closest("li")); };
  }

  // ---------------- activity grid (aggregate distinct-user counts; sparse cells already removed) ----------------
  function niceBreak(v) { const p = 10 ** Math.floor(Math.log10(v)); return Math.max(1, Math.round(v / p * 2) / 2 * p); }
  // Counts are heavy-tailed (median ~25, max ~1500), so classes step geometrically from the
  // suppression floor to the max; quantile classes would paint most of the city the same colour.
  function breaks(lo, values) {
    const hi = Math.max(...values), ratio = (hi / lo) ** (1 / 5), out = [];
    for (let k = 1; k < 5; k++) { const b = niceBreak(lo * ratio ** k); if (b > (out.at(-1) ?? lo)) out.push(b); }
    return out; // class k covers [breaks[k-1], breaks[k])
  }
  const OPACITY = [0, 0.32, 0.45, 0.58, 0.7, 0.8]; // fainter where few people were
  function renderHeat() {
    heatLayer.clearLayers();
    const g = S.data.activity_grid, mode = S.heatMode;
    for (const b of $("fl-heatmode").children) b.classList.toggle("on", b.dataset.m === mode);
    if (mode === "off") { $("fl-heatkey").innerHTML = ""; syncLayer(); return; }
    const key = mode === "event" ? "event_users" : "users";
    const cells = g.cells.filter(c => c[key] != null);
    const lo = mode === "event" ? g.min_users_day : g.min_users_period;
    const br = breaks(lo, cells.map(c => c[key])), [dLat, dLon] = g.cell_deg;
    const cls = v => 1 + br.filter(b => v >= b).length;
    const what = mode === "event" ? "on Aug 12" : `over ${g.days} days`;
    for (const c of cells) {
      const k = cls(c[key]);
      L.rectangle([[c.lat - dLat / 2, c.lon - dLon / 2], [c.lat + dLat / 2, c.lon + dLon / 2]],
        { pane: "floodActivity", className: "heat-" + k, stroke: false, fillOpacity: OPACITY[k] })
        .bindTooltip(`${c[key]} people posted here ${what}`, { sticky: true }).addTo(heatLayer);
    }
    const edges = [lo, ...br];
    $("fl-heatkey").innerHTML = edges.map((e, k) =>
      `<div><i class="heat-${k + 1}" style="background:var(--heat-${k + 1})"></i>${k === edges.length - 1 ? e + "+" : e + "–" + (edges[k + 1] - 1)}</div>`).join("");
    $("fl-heatnote").textContent = `Distinct Twitter users who posted in each ~450 m cell ${what}. Cells with fewer than ${lo} people are hidden. ` +
      `${cells.length} cells shown. This is where people were active, not residents or traffic.`;
    syncLayer();
  }
  $("fl-heatmode").onclick = e => {
    const m = e.target.closest("button")?.dataset.m; if (!m) return;
    S.heatMode = m; try { localStorage.setItem("mci.floodHeat", m); } catch {}
    if (S.data) renderHeat();
  };

  function sources(list) {
    $("fl-sources").innerHTML = list.map(s => `<li><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.label)}</a></li>`).join("");
  }

  async function load() {
    if (S.data) return S.data;
    const json = url => fetch(url).then(r => { if (!r.ok) throw new Error(url + ": HTTP " + r.status); return r.json(); });
    S.loading ??= Promise.all([json("flood_2014.json"), json("/api/flood-2014/closures")]);
    const [d, cl] = await S.loading;
    S.closures = cl.closures;
    S.data = d;
    stats(d.meta); rainChart(d.rain_hourly); activityChart(d.activity_hourly, d.meta);
    dailyChart(d.mobility_daily); reports(d.reports); sources(d.meta.sources); renderHeat(); applyUi();
    return d;
  }

  // ---------------- apply to the simulation ----------------
  const activeClosures = () => S.closures.filter(c => c.time_local.slice(11) <= S.at);
  function applyUi() {
    $("fl-apply").checked = S.apply;
    $("fl-at").value = S.at;
    $("fl-at").disabled = !S.apply;
    const on = activeClosures();
    const names = on.map(c => c.road.split(/,| between| at| near| \(/)[0]).join(", ");
    $("fl-apply-note").innerHTML = S.closures.length
      ? `At ${esc(clock(S.at))}, <b>${on.length} of ${S.closures.length}</b> reported road closures are in effect${on.length ? ": " + esc(names) : ""}.` +
        (S.apply ? " Routes avoid them from now on; run the simulation again to update results." : " Tick the box to apply them.")
      : "";
    const banner = $("floodBanner");
    banner.hidden = !S.apply;
    banner.innerHTML = S.apply
      ? `<b>Real flood applied</b>: Aug 12, 2014, MCI at ${esc(clock(S.at))}. Routes avoid ${on.length} road closures reported by then. <a id="floodBannerLink">Flood settings</a>`
      : "";
    const link = $("floodBannerLink"); if (link) link.onclick = () => setTab("flood");
  }
  function applyChanged() {
    try { localStorage.setItem("mci.floodApply", S.apply ? "1" : ""); localStorage.setItem("mci.floodAt", S.at); } catch {}
    if (S.data) reports(S.data.reports);
    applyUi(); syncLayer();
    window.onFloodChanged?.(); // index.html re-draws the Map tab route with the new closures
  }
  $("fl-apply").onchange = e => { S.apply = e.target.checked; load().then(applyChanged); };
  $("fl-at").onchange = e => { S.at = e.target.value; applyChanged(); };
  $("fl-goto").onclick = () => setTab("sim");

  function syncLayer() {
    const want = S.active || S.apply;
    if (want && !map.hasLayer(layer)) layer.addTo(map);
    if (!want && map.hasLayer(layer)) layer.remove();
    const heat = want && S.heatMode !== "off";
    if (heat && !map.hasLayer(heatLayer)) heatLayer.addTo(map);
    if (!heat && map.hasLayer(heatLayer)) heatLayer.remove();
  }
  applyUi();
  if (S.apply) load().then(() => { syncLayer(); window.onFloodChanged?.(); }).catch(() => {});

  window.floodCase = {
    routingParams() { return S.apply ? { flood_at: S.at } : {}; },
    async activate() {
      S.active = true;
      try {
        await load();
      } catch (err) {
        $("fl-stats").innerHTML = `<p class="small" style="color:var(--bad)">Could not load flood_2014.json (${esc(err.message)}). Run data/flood_2014/build_flood_case.py.</p>`;
        return;
      }
      if (!S.active) return;
      syncLayer();
      if (!S.framed) { S.framed = true; map.fitBounds(L.latLngBounds(S.markers.map(m => m.getLatLng())), { padding: [50, 50] }); }
    },
    deactivate() { S.active = false; syncLayer(); },
  };
  // index.html restores the last tab before this script loads; catch up if that was this tab.
  if (typeof activeTab !== "undefined" && activeTab === "flood") window.floodCase.activate();
})();
