// Refresh the review cards every two seconds so new requests appear without reloading the page.
const box = document.getElementById("cards");
if (box) {
  setInterval(async () => {
    if (box.contains(document.activeElement)) return; // do not refresh under the pointer focus
    const response = await fetch(box.dataset.refresh, { cache: "no-store" });
    if (!response.ok) return;
    const html = await response.text();
    // Replace the cards only when something changed, so the checkpoint does not flicker or lose its scroll.
    if (html !== box.dataset.last) { box.dataset.last = html; box.innerHTML = html; }
  }, 2000);
}
