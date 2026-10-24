// Pont entre la page du portail et l'extension. Tourne uniquement sur les origines
// du portail listées dans manifest.json.
//
//   page  -> extension : window.postMessage({source: "lidl-portal", type: "start", url, state})
//   extension -> page  : window.postMessage({source: "lidl-extension", type: "started" | "result", ...})
//
// La page détecte l'extension grâce à l'attribut data-lidl-extension de <html>.

document.documentElement.dataset.lidlExtension = chrome.runtime.getManifest().version;

const reply = (message) =>
  window.postMessage({ source: "lidl-extension", ...message }, location.origin);

window.addEventListener("message", async (event) => {
  // Seule la page elle-même peut nous parler (pas une iframe, pas une autre origine)
  if (event.source !== window || event.origin !== location.origin) return;
  const msg = event.data;
  if (!msg || msg.source !== "lidl-portal" || msg.type !== "start") return;
  try {
    const res = await chrome.runtime.sendMessage({ type: "start", url: msg.url, state: msg.state });
    reply({ type: "started", ok: !!res?.ok, error: res?.error });
  } catch (e) {
    reply({ type: "started", ok: false, error: String(e) });
  }
});

chrome.runtime.onMessage.addListener((msg) => {
  if (msg?.type === "result") reply({ type: "result", ok: msg.ok, error: msg.error });
});
