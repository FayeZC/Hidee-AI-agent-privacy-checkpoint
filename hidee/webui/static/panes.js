// Resizable workspace columns: drag a gutter to change a column's width; the widths are remembered on this computer.
(() => {
  const panes = document.querySelector(".panes");
  if (!panes) return;
  const KEY = "hidee.panes";
  const DEFAULT = { left: 280, right: 500 };
  const LIMIT = { left: [180, 520], right: [340, 1000] };
  const MIDDLE_MIN = 360, GUTTERS = 14;
  let widths = { ...DEFAULT };
  try { widths = { ...DEFAULT, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch (e) { /* defaults */ }

  function apply() {
    // Never squeeze the terminal below its minimum: take the room back from the side being dragged.
    const room = panes.clientWidth - GUTTERS - MIDDLE_MIN;
    for (const side of ["left", "right"]) {
      const [lo, hi] = LIMIT[side];
      widths[side] = Math.round(Math.max(lo, Math.min(hi, widths[side])));
    }
    if (room > 0 && widths.left + widths.right > room) {
      widths.right = Math.max(LIMIT.right[0], room - widths.left);
      widths.left = Math.max(LIMIT.left[0], room - widths.right);
    }
    panes.style.setProperty("--left", `${widths.left}px`);
    panes.style.setProperty("--right", `${widths.right}px`);
  }

  for (const gutter of panes.querySelectorAll(".gutter")) {
    const side = gutter.dataset.side;
    gutter.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      gutter.setPointerCapture(e.pointerId);
      gutter.classList.add("drag");
      document.body.classList.add("dragging");
      const startX = e.clientX, start = widths[side];
      const move = (ev) => {
        const dx = ev.clientX - startX;
        widths[side] = side === "left" ? start + dx : start - dx;
        apply();
      };
      const up = () => {
        gutter.classList.remove("drag");
        document.body.classList.remove("dragging");
        gutter.removeEventListener("pointermove", move);
        gutter.removeEventListener("pointerup", up);
        gutter.removeEventListener("pointercancel", up);
        localStorage.setItem(KEY, JSON.stringify(widths));
      };
      gutter.addEventListener("pointermove", move);
      gutter.addEventListener("pointerup", up);
      gutter.addEventListener("pointercancel", up);
    });
    gutter.addEventListener("dblclick", () => {
      widths[side] = DEFAULT[side];
      apply();
      localStorage.setItem(KEY, JSON.stringify(widths));
    });
    // Keyboard: arrow keys move the gutter by 20 px.
    gutter.tabIndex = 0;
    gutter.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      const dx = e.key === "ArrowLeft" ? -20 : 20;
      widths[side] += side === "left" ? dx : -dx;
      apply();
      localStorage.setItem(KEY, JSON.stringify(widths));
    });
  }
  // The rule between the live flow and the checkpoint, in the right column: a share of the column's height.
  const column = panes.querySelector(".checkpoint"), rule = panes.querySelector(".gutter-h");
  if (column && rule) {
    const HKEY = "hidee.flow", HDEFAULT = 45;
    let share = Number(localStorage.getItem(HKEY)) || HDEFAULT;
    const set = () => { share = Math.max(15, Math.min(80, share)); column.style.setProperty("--flow", `${share}%`); };
    rule.addEventListener("pointerdown", (e) => {
      e.preventDefault();
      rule.setPointerCapture(e.pointerId);
      rule.classList.add("drag");
      document.body.classList.add("dragging-h");
      const box = column.getBoundingClientRect();
      const move = (ev) => { share = ((ev.clientY - box.top) / box.height) * 100; set(); };
      const up = () => {
        rule.classList.remove("drag");
        document.body.classList.remove("dragging-h");
        rule.removeEventListener("pointermove", move);
        rule.removeEventListener("pointerup", up);
        rule.removeEventListener("pointercancel", up);
        localStorage.setItem(HKEY, String(Math.round(share)));
      };
      rule.addEventListener("pointermove", move);
      rule.addEventListener("pointerup", up);
      rule.addEventListener("pointercancel", up);
    });
    rule.addEventListener("dblclick", () => { share = HDEFAULT; set(); localStorage.setItem(HKEY, String(share)); });
    rule.tabIndex = 0;
    rule.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
      share += e.key === "ArrowUp" ? -4 : 4; set(); localStorage.setItem(HKEY, String(Math.round(share)));
    });
    set();
    // The flow area keeps room for a scrollbar, and the checkpoint page has one too; shift the checkpoint's
    // heading by the same width so its right edge lines up with the content above and below it.
    const flow = column.querySelector(".flow-part");
    const align = () => column.style.setProperty("--sbw", `${Math.max(0, flow.offsetWidth - flow.clientWidth)}px`);
    new ResizeObserver(align).observe(flow);
    align();
  }
  addEventListener("resize", apply);
  apply();
})();
