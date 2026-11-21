// Learn mode: poll the session's timeline and show each step as a card, grouped by turn.
(() => {
  const list = document.getElementById("steps");
  const session = list.dataset.session;
  const TITLE = { prompt: "You asked", call: "The model chose a tool", result: "The tool returned",
                  failure: "The tool failed", answer: "The model answered" };
  const WHERE = { local: "Stays on this computer", model: "Goes to the model provider",
                  outside: "Leaves this computer" };
  const LABEL = { email: "email address", phone: "phone number", api_key: "API key or token",
                  card_number: "card number", government_id: "government ID", health: "health information",
                  money: "money or pay", internal: "internal information" };
  let since = 0, inContext = 0, turn = 0;
  const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls;
                                   if (text !== undefined) n.textContent = text; return n; };
  const add = (e) => {
    const empty = list.querySelector(".bh-empty");
    if (empty) empty.remove();
    if (e.kind === "prompt") { turn += 1; list.append(el("li", "bh-turn", `Turn ${turn}`)); }
    const li = el("li", `bh-step ${e.kind} ${e.where}`);
    const head = el("div", "bh-head");
    head.append(el("b", "", TITLE[e.kind] + (e.tool && e.kind !== "prompt" && e.kind !== "answer" ? `: ${e.tool}` : "")));
    const where = e.kind === "answer" ? "Comes from the model provider" : (WHERE[e.where] || e.where);
    head.append(el("span", `bh-where ${e.where}`, where));
    li.append(head, el("p", "bh-why", e.why));
    if (e.detail && e.kind === "call") li.append(el("p", "bh-detail", e.detail));
    if (e.text && e.kind !== "call") {
      const pre = el("pre", "bh-text", e.text);
      li.append(pre);
      if (e.size > e.text.length) li.append(el("p", "meta", `${e.size.toLocaleString()} characters in all; the first part is shown.`));
    }
    const found = (e.found || []).filter((f) => LABEL[f]);
    if (found.length) li.append(el("p", "bh-found", "Contains: " + found.map((f) => LABEL[f]).join(", ")));
    list.append(li);
    if (e.where === "model") inContext += e.size || 0;
  };
  const tick = async () => {
    try {
      const r = await fetch(`/learn/${encodeURIComponent(session)}?since=${since}`);
      const data = await r.json();
      const first = since === 0;
      const atBottom = !first && window.innerHeight + window.scrollY >= document.body.scrollHeight - 40;
      for (const e of data.events) { add(e); since = e.seq; }
      const n = (x) => x.toLocaleString();
      const u = data.usage;
      if (u) {
        // Real numbers from the agent's own transcript.
        document.getElementById("meter-label").textContent = "The model's latest request";
        document.getElementById("meter-chars").textContent = `${n(u.last_input)} tokens`;
        document.getElementById("meter-tokens").textContent =
          `${n(u.requests)} request${u.requests === 1 ? "" : "s"} so far · the model wrote ${n(u.output)} tokens`;
        document.getElementById("meter-note").textContent =
          `Your words are a small part of it. The first request already held ${n(u.first_input)} tokens: ` +
          "mostly the agent's own instructions and the descriptions of its tools.";
      } else {
        document.getElementById("meter-chars").textContent = `${n(inContext)} characters`;
        document.getElementById("meter-tokens").textContent = `about ${n(Math.round(inContext / 4))} tokens (a rough estimate)`;
      }
      if (data.events.length && atBottom) window.scrollTo(0, document.body.scrollHeight);
    } catch (err) { /* the app may be restarting; try again */ }
  };
  tick();
  setInterval(tick, 1500);

  // A term link (such as "model provider") opens the glossary at that word.
  const glossary = document.getElementById("glossary");
  const openTerm = () => {
    const target = location.hash && document.getElementById(location.hash.slice(1));
    if (!glossary || !target || !glossary.contains(target)) return;
    glossary.open = true;
    glossary.querySelectorAll("dl > div.on").forEach((d) => d.classList.remove("on"));
    target.classList.add("on");
    target.scrollIntoView({ block: "nearest" });
  };
  window.addEventListener("hashchange", openTerm);
  document.querySelectorAll("a.term").forEach((a) => a.addEventListener("click", () => setTimeout(openTerm)));
  openTerm();
})();
