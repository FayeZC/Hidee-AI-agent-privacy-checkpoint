// New session: typing a project name or choosing a folder selects that option; "Choose…" opens the system's
// own folder chooser on this computer.
(() => {
  const form = document.getElementById("start-form");
  if (!form) return;
  const pick = (value) => { form.querySelector(`input[name=where][value=${value}]`).checked = true; };
  form.project.addEventListener("focus", () => pick("new"));
  form.cwd.addEventListener("focus", () => pick("open"));
  const button = document.getElementById("pick-folder");
  button.addEventListener("click", async () => {
    pick("open");
    button.disabled = true;
    try {
      const body = new URLSearchParams({ csrf: form.csrf.value });
      const r = await fetch("/pick-folder", { method: "POST", body });
      const data = await r.json();
      if (data.path) form.cwd.value = data.path;
      else if (data.unsupported) form.cwd.focus();
    } finally {
      button.disabled = false;
    }
  });
  form.addEventListener("submit", (e) => {
    if (form.kind.value === "practice") return;      // a practice lesson brings its own fictional folder
    const where = form.where.value;
    if (where === "open" && !form.cwd.value.trim()) { e.preventDefault(); button.click(); }
  });
  // A practice lesson hides the folder, sign-in, and options it does not use.
  const note = document.getElementById("practice-note");
  const showKind = () => {
    const practice = form.kind.value === "practice";
    form.classList.toggle("practice", practice);
    if (note) note.hidden = !practice;
  };
  form.querySelectorAll("input[name=kind]").forEach((r) => r.addEventListener("change", showKind));
  showKind();
})();
