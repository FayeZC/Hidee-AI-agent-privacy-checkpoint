// Live flow map: sessions → Hidee → where each tool call goes. Every call moves along the map as it happens;
// local work goes to "This computer", outbound calls to their service, coloured by what Hidee decided.
(() => {
  const box = document.getElementById("live");
  if (!box) return;
  const NS = "http://www.w3.org/2000/svg";
  // Geometry: a narrow session column, the Hidee mark, and a wide destination column with room to explain.
  const S_W = 96, MARK = 44, D_GAP = 6, D_H = 66;
  let W = 452, H = 260;
  const hubX = () => S_W + 30, destX = () => S_W + 106;
  const COLOR = { local: "#8A8A8A", allowed: "#008A4E", minimized: "#1F3BFF", blocked: "#D8141A",
                  timed_out: "#D8141A", execution_failed: "#D8141A", waiting: "#E85300" };
  const WORD = { local: "local", allowed: "allowed", minimized: "shorter version", blocked: "blocked",
                 timed_out: "timed out", execution_failed: "failed", waiting: "waiting for you" };
  const MAX_DESTS = 5;
  const RISK_WORD = { privacy: "privacy", security: "security", internal: "internal", overreach: "overreach" };
  const riskCount = { privacy: new Set(), security: new Set(), internal: new Set(), overreach: new Set() };

  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Live map of where each tool call goes");
  box.appendChild(svg);
  const list = document.getElementById("live-list");

  let seq = 0, names = {}, kinds = {};
  const waiting = new Set();                 // actions waiting for a decision
  const sessions = [];                       // session ids, first seen first
  const dests = new Map();                   // recipient -> {count, status, at, risks, last}
  const latest = new Map();                  // call key -> {recipient, status}: one entry per call
  const pending = new Map();                 // action id -> marker element waiting at the hub
  const recent = [];                         // newest first, for the list

  const label = (r) => r === "this-computer" ? "This computer" : r === "web-search" ? "Web search"
    : r === "model-provider" ? "Model provider"
    : r.startsWith("mcp:") ? `MCP · ${r.slice(4)}` : r;
  const cut = (s, n) => s.length > n ? s.slice(0, n - 1) + "…" : s;
  const el = (tag, attrs, parent) => {
    const e = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    (parent || svg).appendChild(e);
    return e;
  };
  const text = (x, y, s, cls, parent) => { const t = el("text", { x, y, class: cls }, parent); t.textContent = s; return t; };

  function layout() {
    const shownSessions = sessions.slice(-4);
    const shownDests = ["this-computer", ...[...dests.keys()].filter((d) => d !== "this-computer")
      .sort((a, b) => dests.get(b).at - dests.get(a).at).slice(0, MAX_DESTS - 1)];
    const h = Math.max(220, shownDests.length * (D_H + D_GAP) - D_GAP + 4);
    const place = (items, size, gap) => {
      const total = items.length * size + (items.length - 1) * gap, top = (h - total) / 2;
      return new Map(items.map((id, i) => [id, top + i * (size + gap)]));
    };
    return { h, s: place(shownSessions, 54, 12), d: place(shownDests, D_H, D_GAP) };
  }

  function statusLine(id) {
    const counts = {};
    for (const v of latest.values()) if (v.recipient === id) counts[v.status] = (counts[v.status] || 0) + 1;
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const parts = [`${total} call${total === 1 ? "" : "s"}`];
    const blocked = (counts.blocked || 0) + (counts.timed_out || 0) + (counts.execution_failed || 0);
    if (counts.waiting) parts.push(`${counts.waiting} waiting`);
    if (blocked) parts.push(`${blocked} blocked`);
    if (counts.minimized) parts.push(`${counts.minimized} shortened`);
    return parts.join(" · ");
  }

  let geo = null;
  function draw() {
    svg.textContent = "";
    W = Math.max(380, Math.round(box.clientWidth || 452));
    geo = layout();
    H = geo.h;
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    const hubMid = H / 2, hx = hubX(), dx = destX(), dw = W - dx, chars = Math.floor((dw - 36) / 7.3), metaChars = Math.floor((dw - 36) / 6.7);
    const curve = (x1, y1, x2, y2) => `M${x1},${y1} C${(x1 + x2) / 2},${y1} ${(x1 + x2) / 2},${y2} ${x2},${y2}`;
    geo.paths = { in: new Map(), out: new Map() };
    geo.hold = { x: hx + MARK - 4, y: hubMid - MARK / 2 - 16 };
    for (const [id, y] of geo.s) geo.paths.in.set(id, el("path", { d: curve(S_W, y + 27, hx, hubMid), class: "edge" }));
    for (const [id, y] of geo.d) {
      const d = dests.get(id) || { status: "local", count: 0 };
      geo.paths.out.set(id, el("path", { d: curve(hx + MARK, hubMid, dx, y + D_H / 2),
                                         class: `edge out ${d.status === "waiting" ? "wait" : ""}`,
                                         stroke: d.count ? COLOR[d.status] || COLOR.local : "#D6D6D6" }));
    }
    for (const [id, y] of geo.s) {
      const calls = [...latest.values()].filter((v) => v.session === id).length;
      el("rect", { x: 0.5, y: y + 0.5, width: S_W - 1, height: 53, class: "node" });
      const name = names[id] || id;
      const kind = { shell: "Shell", claude: "Claude Code", gemini: "Gemini CLI", codex: "Codex CLI",
                     practice: "Practice" }[kinds[id] || "claude"] || "AI agent";
      // A long name takes two lines (in place of the agent line) instead of being cut to one word.
      const cutAt = name.length > 10 ? name.lastIndexOf(" ", 11) : -1;
      if (cutAt > 0) {
        text(9, y + 17, name.slice(0, cutAt), "n-name");
        text(9, y + 31, cut(name.slice(cutAt + 1), 10), "n-name");
      } else {
        text(9, y + 18, cut(name, 10), "n-name");
        text(9, y + 33, kind, "n-meta");
      }
      text(9, y + 46, `${calls} call${calls === 1 ? "" : "s"}`, "n-meta");
    }
    if (!geo.s.size) text(0, hubMid + 4, "No session yet", "n-meta");
    // The hub is the Hidee mark: an H on a three-row grid with one cell held back in blue.
    const m = el("g", { transform: `translate(${hx},${hubMid - MARK * 224 / 240 / 2}) scale(${MARK / 240})` });
    el("rect", { x: -20, y: -20, width: 280, height: 264, fill: "#fff" }, m);
    el("rect", { x: 0, y: 0, width: 76, height: 224, fill: "#0A0A0A" }, m);
    el("rect", { x: 76, y: 75, width: 88, height: 74, fill: "#0A0A0A" }, m);
    el("rect", { x: 164, y: 75, width: 76, height: 149, fill: "#0A0A0A" }, m);
    el("rect", { x: 164, y: 0, width: 76, height: 75, fill: "#1F3BFF" }, m);
    text(hx + MARK / 2, hubMid + MARK / 2 + 16, "HIDEE", "hub-label");
    for (const [id, y] of geo.d) {
      const d = dests.get(id) || { status: "local", count: 0 };
      const g = el("g", d.step ? { "data-step": d.step, tabindex: 0 } : {});
      el("rect", { x: dx + 0.5, y: y + 0.5, width: dw - 1, height: D_H - 1, class: "node" }, g);
      el("rect", { x: dx + 9, y: y + 9, width: 9, height: 9, fill: d.count ? COLOR[d.status] || COLOR.local : "#D6D6D6" }, g);
      text(dx + 25, y + 17, cut(label(id), chars), "n-name small", g);
      text(dx + 25, y + 31, cut(d.count ? statusLine(id) : "nothing yet", metaChars), "n-meta", g);
      if (d.risks && d.risks.size)
        text(dx + 25, y + 44, cut([...d.risks].map((r) => RISK_WORD[r]).join(" · "), metaChars), "n-meta risky", g);
      else if (d.count) text(dx + 25, y + 44, "no risk found", "n-meta faint", g);
      if (d.last) text(dx + 25, y + 57, cut(`last · ${d.last}`, metaChars), "n-meta faint", g);
    }
  }

  function travel(path, color, ms, done) {
    if (!path) { done && done(null); return; }
    const len = path.getTotalLength(), dot = el("rect", { width: 9, height: 9, fill: color, class: "dot" });
    const start = performance.now();
    const step = (now) => {
      const k = Math.min(1, (now - start) / ms), pt = path.getPointAtLength(len * k);
      dot.setAttribute("x", pt.x - 4.5); dot.setAttribute("y", pt.y - 4.5);
      if (k < 1) requestAnimationFrame(step); else done ? done(dot) : dot.remove();
    };
    requestAnimationFrame(step);
  }

  function animate(e) {
    const color = COLOR[e.status] || COLOR.local;
    const inPath = geo.paths.in.get(e.session), outPath = geo.paths.out.get(e.recipient);
    if (e.action && pending.has(e.action)) {          // a decision for a call that was waiting at the hub
      pending.get(e.action).remove(); pending.delete(e.action);
      travel(outPath, color, 650);
      return;
    }
    travel(inPath, e.status === "waiting" ? COLOR.waiting : "#0A0A0A", 550, (dot) => {
      if (dot) dot.remove();
      if (e.status === "waiting") {
        const hold = el("rect", { x: geo.hold.x, y: geo.hold.y, width: 10, height: 10, fill: COLOR.waiting, class: "dot pulse" });
        pending.set(e.action, hold);
      } else travel(outPath, color, 650);
    });
  }

  function renderWaiting() {
    // While something waits, a notice above the checkpoint points down to it.
    const pane = box.closest(".checkpoint");
    if (pane) pane.classList.toggle("waiting", waiting.size > 0);
    const note = document.getElementById("waiting-note");
    if (note) note.textContent = waiting.size ? `${waiting.size} waiting for you ↓` : "";
  }

  function renderRisks() {
    renderWaiting();
    for (const [risk, ids] of Object.entries(riskCount)) {
      const cell = document.querySelector(`#risk-bar [data-risk="${risk}"]`);
      if (!cell) continue;
      cell.querySelector("b").textContent = ids.size;
      cell.classList.toggle("on", ids.size > 0);
    }
  }

  function renderList() {
    renderRisks();
    if (!list) return;
    list.textContent = "";
    for (const e of recent.slice(0, 5)) {
      const li = document.createElement("li");
      li.dataset.step = e.action ? `/step/${e.action}` : `/step/event/${e.seq}`;
      li.tabIndex = 0;
      li.title = "Show this step as a picture";
      const t = new Date(e.at * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
      li.innerHTML = `<span class="sq"></span><span class="what"></span><span class="when"></span><span class="where"></span>`;
      li.querySelector(".sq").style.background = COLOR[e.status] || COLOR.local;
      li.querySelector(".what").textContent = e.text;
      li.querySelector(".where").textContent = `→ ${label(e.recipient)} · ${WORD[e.status] || e.status}` +
        (e.categories.length ? ` · ${e.categories.join(", ").replaceAll("_", " ")}` : "");
      li.querySelector(".when").textContent = t;
      for (const r of e.risks) {
        const tag = document.createElement("span");
        tag.className = "risk"; tag.textContent = RISK_WORD[r];
        li.querySelector(".where").prepend(tag);
      }
      list.appendChild(li);
    }
    if (!recent.length) list.innerHTML = '<li class="none">Tool calls appear here as they happen.</li>';
  }

  function absorb(e) {
    if (!sessions.includes(e.session)) sessions.push(e.session);
    const d = dests.get(e.recipient) || { count: 0, risks: new Set() };
    const same = e.action && recent.find((r) => r.action === e.action);
    e.risks.forEach((r) => { d.risks.add(r); riskCount[r].add(e.action || `seq${e.seq}`); });
    dests.set(e.recipient, { count: d.count + (same ? 0 : 1), status: e.status, at: e.at, risks: d.risks,
                             last: e.text, step: e.action ? `/step/${e.action}` : `/step/event/${e.seq}` });
    latest.set(e.action || `seq${e.seq}`, { recipient: e.recipient, status: e.status, session: e.session });
    if (same) recent.splice(recent.indexOf(same), 1);
    if (e.action) { if (e.status === "waiting") waiting.add(e.action); else waiting.delete(e.action); }
    recent.unshift(e);
  }

  async function poll(first) {
    try {
      const r = await fetch(`/live?since=${seq}`, { cache: "no-store" });
      if (r.ok) {
        const data = await r.json();
        names = Object.fromEntries(Object.entries(data.sessions).map(([k, v]) => [k, v.name]));
        kinds = Object.fromEntries(Object.entries(data.sessions).map(([k, v]) => [k, v.kind]));
        for (const id of Object.keys(names)) if (!sessions.includes(id)) sessions.push(id);
        const before = JSON.stringify(layout());
        data.events.forEach(absorb);
        if (data.events.length) seq = data.events[data.events.length - 1].seq;
        if (Array.isArray(data.waiting)) {     // the store has the last word on what is waiting
          const truth = new Set(data.waiting);
          for (const id of [...waiting]) {
            if (truth.has(id)) continue;
            waiting.delete(id);
            if (pending.has(id)) { pending.get(id).remove(); pending.delete(id); }
          }
          renderWaiting();
        }
        if (first || JSON.stringify(layout()) !== before || data.events.length) {
          draw();
          for (const [action, dot] of pending) svg.appendChild(dot);   // keep waiting markers after a redraw
        }
        if (!first) data.events.forEach(animate);
        renderList();
      }
    } catch (_) { /* the app restarted or the network blinked; try again on the next tick */ }
    setTimeout(poll, 700);
  }
  addEventListener("resize", () => { if (geo) draw(); });
  // Columns can be dragged wider or narrower: redraw when the map's own box changes width.
  let lastWidth = 0;
  new ResizeObserver(() => {
    const w = Math.round(box.clientWidth);
    if (geo && w && w !== lastWidth) { lastWidth = w; draw(); }
  }).observe(box);
  poll(true);
})();
