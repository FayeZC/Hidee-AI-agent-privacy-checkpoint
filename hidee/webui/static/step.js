// Step pop-up: click anything marked data-step to see that step as a data-flow picture, without leaving the page.
(() => {
  let layer = null, lastFocus = null;
  const close = () => {
    if (!layer) return;
    layer.remove(); layer = null;
    document.body.classList.remove("step-open");
    if (lastFocus) lastFocus.focus();
  };
  async function open(url) {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) return;
    close();
    lastFocus = document.activeElement;
    layer = document.createElement("div");
    layer.className = "step-layer";
    layer.innerHTML = await r.text();
    layer.addEventListener("click", (e) => {
      if (e.target === layer || e.target.closest(".step-close")) close();
      // Already in the workspace: "Decide in the checkpoint" just closes the sheet and points at the pane.
      const go = e.target.closest("a[href]");
      if (go && go.getAttribute("href") === location.pathname) {
        e.preventDefault(); close();
        const pane = document.querySelector(".checkpoint iframe");
        if (pane) { pane.scrollIntoView({ behavior: "smooth", block: "center" }); pane.focus(); }
      }
    });
    document.body.appendChild(layer);
    document.body.classList.add("step-open");
    layer.querySelector(".step-close").focus();
  }
  document.addEventListener("click", (e) => {
    const target = e.target.closest("[data-step]");
    if (!target || e.metaKey || e.ctrlKey) return;
    e.preventDefault();
    open(target.dataset.step);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
    const target = e.target.closest && e.target.closest("[data-step]");
    if (target && (e.key === "Enter" || e.key === " ") && target.tagName !== "A") { e.preventDefault(); open(target.dataset.step); }
  });
  window.hideeOpenStep = open;
})();
