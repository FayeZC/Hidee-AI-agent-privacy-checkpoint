// Connect the terminal pane to its session over a WebSocket. The session keeps running if this page closes.
const box = document.getElementById("term");
if (box) {
  const term = new Terminal({
    fontFamily: '"JetBrains Mono", Menlo, monospace', fontSize: 14, lineHeight: 1.2, cursorBlink: true,
    theme: { background: "#000000", foreground: "#F2F2F2", cursor: "#1F3BFF", selectionBackground: "#1F3BFF66",
             scrollbarSliderBackground: "#FFFFFF22", scrollbarSliderHoverBackground: "#FFFFFF44",
             scrollbarSliderActiveBackground: "#FFFFFF66" },
  });
  const fit = new FitAddon.FitAddon();
  term.loadAddon(fit);
  term.open(box);
  window.hideeTerm = term;   // handy for debugging from the browser console
  fit.fit();

  const scheme = location.protocol === "https:" ? "wss" : "ws";
  const url = `${scheme}://${location.host}/term/${box.dataset.session}?key=${encodeURIComponent(box.dataset.key)}`;
  let ws;
  const send = (msg) => { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg)); };
  const resize = () => { fit.fit(); send({ type: "resize", rows: term.rows, cols: term.cols }); };

  const connect = () => {
    ws = new WebSocket(url);
    ws.onopen = () => { term.reset(); resize(); term.focus(); };
    ws.onmessage = (e) => term.write(e.data);
    ws.onclose = () => { term.write("\r\n\x1b[90m[disconnected; reconnecting…]\x1b[0m\r\n"); setTimeout(connect, 1500); };
  };
  term.onData((data) => send({ type: "input", data }));
  new ResizeObserver(resize).observe(box);
  connect();
}
