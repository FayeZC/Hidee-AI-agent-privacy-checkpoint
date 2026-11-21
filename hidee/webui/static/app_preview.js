// A scripted replay of one agent session, to show how Hidee fits into everyday agent work.
// Nothing here runs a real terminal or sends anything.
const term = document.getElementById("terminal");
const card = document.getElementById("hidee-card");
const idle = document.getElementById("hidee-idle");
const confirmBox = document.getElementById("hidee-confirm");
const sessionState = document.getElementById("s-email-state");
const stats = { checked: document.getElementById("stat-checked"), asked: document.getElementById("stat-asked") };
let timers = [];

const SCRIPT_BEFORE = [
  ["$ ", "t-dim", "claude", "t-user", 400],
  ["╭─ Claude Code ─────────────────────────────╮", "t-box", "", "", 300],
  ["│ ", "t-box", "cwd: ~/thesis-project", "t-dim", 200],
  ["╰───────────────────────────────────────────╯", "t-box", "", "", 500],
  ["> ", "t-dim", "Summarize this week's results and email them to Prof. Rivera.", "t-user", 900],
  ["", "", "", "", 300],
  ["● ", "t-ok", "Read results/summary.csv", "", 700],
  ["● ", "t-ok", "Read notes/week9.md", "", 700],
  ["● ", "t-ok", "Search the web: \"F1 score definition\"   ", "", 0],
  ["", "", "  ✓ Hidee: allowed (public information)", "t-dim", 900],
  ["● ", "t-ok", "Draft email to the accountant", "", 900],
  ["● ", "t-warn", "Send email (gmail.send)", "", 400],
  ["  ", "", "⏸ Waiting for Hidee: you have a decision to make", "t-warn", 0],
];

const AFTER = {
  minimal: [["  ✓ ", "t-ok", "Hidee: sent the shorter version (results summary only)", "t-ok"],
            ["● ", "t-ok", "Email sent to Prof. Rivera.", ""]],
  approve: [["  ✓ ", "t-ok", "Hidee: approved as written", "t-ok"],
            ["● ", "t-ok", "Email sent to Prof. Rivera.", ""]],
  reject: [["  ✕ ", "t-bad", "Hidee: you rejected this email", "t-bad"],
           ["● ", "t-dim", "Okay, I won't send it. Want me to save the draft instead?", ""]],
};

const CONFIRM = {
  minimal: ["Send the shorter version to Prof. Rivera?",
            "Only the results summary is sent. The student ID and health information stay on your computer."],
  approve: ["Send it as written to Prof. Rivera?",
            "The results summary, your student ID, and the health note are all sent."],
  reject: ["Reject this email?", "Nothing is sent. Claude Code is told you said no."],
};

function line(prefix, prefixCls, text, textCls) {
  const row = document.createElement("div");
  if (prefix) { const p = document.createElement("span"); p.className = prefixCls; p.textContent = prefix; row.appendChild(p); }
  if (text) { const t = document.createElement("span"); t.className = textCls; t.textContent = text; row.appendChild(t); }
  if (!prefix && !text) row.innerHTML = "&nbsp;";
  term.appendChild(row);
  term.scrollTop = term.scrollHeight;
}

function cursor(on) {
  term.querySelectorAll(".cursor").forEach(c => c.remove());
  if (on) { const c = document.createElement("span"); c.className = "cursor"; term.appendChild(c); }
}

function later(ms, fn) { timers.push(setTimeout(fn, ms)); }

function start() {
  timers.forEach(clearTimeout); timers = [];
  term.innerHTML = ""; card.hidden = true; confirmBox.hidden = true; idle.hidden = false;
  card.querySelectorAll("li").forEach(li => li.classList.remove("cut"));
  sessionState.className = "s-state"; sessionState.innerHTML = '<span class="dot ok"></span>Working';
  stats.checked.textContent = "11"; stats.asked.textContent = "0";
  let t = 300;
  SCRIPT_BEFORE.forEach(([p, pc, text, tc, pause], i) => {
    later(t, () => {
      cursor(false); line(p, pc, text, tc); cursor(true);
      if (text.startsWith("  ✓ Hidee")) stats.checked.textContent = "12";
      if (i === SCRIPT_BEFORE.length - 1) needsYou();
    });
    t += pause || 500;
  });
}

function needsYou() {
  idle.hidden = true; card.hidden = false;
  sessionState.className = "s-state needs"; sessionState.innerHTML = '<span class="dot warn"></span>Needs you';
  stats.checked.textContent = "13"; stats.asked.textContent = "1";
}

let pending = null;
card.querySelectorAll("[data-choice]").forEach(btn => btn.addEventListener("click", () => {
  pending = btn.dataset.choice;
  document.getElementById("confirm-question").textContent = CONFIRM[pending][0];
  document.getElementById("confirm-detail").textContent = CONFIRM[pending][1];
  const yes = document.getElementById("confirm-yes");
  yes.textContent = pending === "reject" ? "Yes, reject it" : "Yes, send it";
  yes.className = "btn " + (pending === "reject" ? "danger" : "primary");
  if (pending === "minimal") card.querySelectorAll("li").forEach((li, i) => li.classList.toggle("cut", i > 0));
  card.hidden = true; confirmBox.hidden = false;
}));

document.getElementById("confirm-back").addEventListener("click", () => {
  card.querySelectorAll("li").forEach(li => li.classList.remove("cut"));
  confirmBox.hidden = true; card.hidden = false;
});

document.getElementById("confirm-yes").addEventListener("click", () => {
  confirmBox.hidden = true; idle.hidden = false;
  idle.innerHTML = pending === "reject"
    ? '<p class="d-big">Nothing was sent.</p><p class="meta">The email stays a draft on your computer.</p>'
    : '<p class="d-done">✓ Sent</p><p class="meta">' + (pending === "minimal"
        ? "Only the results summary left your computer." : "The full email left your computer.") + "</p>";
  sessionState.className = "s-state"; sessionState.innerHTML = '<span class="dot ok"></span>Working';
  cursor(false);
  AFTER[pending].forEach(([p, pc, text, tc], i) => later(400 + i * 700, () => { cursor(false); line(p, pc, text, tc); cursor(true); }));
});

document.getElementById("replay").addEventListener("click", () => {
  idle.innerHTML = '<p class="d-big">Watching quietly.</p><p class="meta">Safe actions go through without asking. You are only asked when something sensitive is about to leave.</p>';
  start();
});
document.getElementById("guide-toggle").addEventListener("click", e => {
  document.body.classList.toggle("no-guide");
  e.target.textContent = document.body.classList.contains("no-guide") ? "Show guide" : "Hide guide";
});

start();
