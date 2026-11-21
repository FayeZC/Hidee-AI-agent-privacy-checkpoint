// hidee.net motion: layers rise into place as you scroll, the screenshot opens up, and the "How it works" drawing
// changes state with each step. Everything is plain CSS driven by a few classes and one variable.
(() => {
  const head = document.querySelector(".site-top");
  if (head) document.documentElement.style.setProperty("--head", head.offsetHeight + "px");
  const still = matchMedia("(prefers-reduced-motion: reduce)").matches;
  // On a phone the page is one column: the map stays flat, the story shows its last state, nothing tilts.
  const narrow = matchMedia("(max-width: 960px)");
  const stage = document.querySelector(".story-stage");
  const steps = [...document.querySelectorAll(".story-step")];
  const frame = document.querySelector("[data-scroll-scale]");

  // 0. Space: the hero map folds into planes and back on its own. The pointer can step in: resting on the map
  // folds it at once and holds it in space; leaving lays it flat and the loop picks up again from there.
  const space = document.querySelector(".space-stage");
  if (space && !still) {
    const EVERY = 3600;
    let timer = null;
    const loop = (delay) => { clearInterval(timer); timer = setTimeout(function tick() {
      space.classList.toggle("space", narrow.matches ? false : !space.classList.contains("space"));
      timer = setTimeout(tick, EVERY); }, delay); };
    space.addEventListener("pointerenter", (e) => {
      if (e.pointerType !== "mouse") return;
      clearTimeout(timer); space.classList.add("space"); });
    space.addEventListener("pointerleave", (e) => {
      if (e.pointerType !== "mouse") return;
      space.classList.remove("space"); loop(EVERY); });
    loop(1200);
  }

  if (still) {
    document.querySelectorAll("[data-reveal]").forEach((el) => el.classList.add("in"));
    if (stage) stage.dataset.step = "3";
    steps.forEach((s) => s.classList.add("on"));
    document.querySelectorAll("[data-scroll-scale], [data-tilt]").forEach((el) => el.style.setProperty("--p", 1));
    return;
  }

  // 1. Layers: each marked element rises in once, with its own small delay (--i).
  const rise = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add("in"); rise.unobserve(e.target); }
  }, { rootMargin: "0px 0px -12% 0px" });
  document.querySelectorAll("[data-reveal]").forEach((el) => rise.observe(el));

  // 2. Story: the step whose middle is nearest the middle of the screen sets the drawing's state.
  if (stage && steps.length) {
    let queued = false;
    const choose = () => {
      queued = false;
      if (narrow.matches) { stage.dataset.step = "3"; steps.forEach((s) => s.classList.add("on")); return; }
      const mid = innerHeight / 2;
      let best = steps[0], gap = Infinity;
      for (const s of steps) {
        const r = s.querySelector("h3").getBoundingClientRect(), d = Math.abs(r.top - mid);
        if (d < gap) { gap = d; best = s; }
      }
      if (stage.dataset.step !== best.dataset.step) stage.dataset.step = best.dataset.step;
      steps.forEach((s) => s.classList.toggle("on", s === best));
    };
    addEventListener("scroll", () => { if (!queued) { queued = true; requestAnimationFrame(choose); } }, { passive: true });
    narrow.addEventListener("change", choose);
    choose();
  }

  // 3. The screenshot and the big panels tilt up to face you as they come into view.
  const tilted = [...document.querySelectorAll("[data-scroll-scale], [data-tilt]")];
  if (tilted.length) {
    let ticking = false;
    const update = () => {
      const h = innerHeight;
      for (const el of tilted) {
        const r = el.getBoundingClientRect();
        const p = Math.min(1, Math.max(0, (h - r.top) / (h * 0.7)));
        el.style.setProperty("--p", p.toFixed(3));
      }
      ticking = false;
    };
    addEventListener("scroll", () => { if (!ticking) { ticking = true; requestAnimationFrame(update); } }, { passive: true });
    addEventListener("resize", update);
    update();
  }
})();
