// Service worker : ouvre la page de connexion Lidl demandée par le portail, repère la
// redirection finale com.lidlplus.app://callback?code=...&state=... et renvoie le code
// au portail. Il ne lit jamais le contenu des pages Lidl.
//
// Sécurité : on ne capte QUE la connexion que le portail vient de demander (message
// venu du content script, sur une origine du portail), dans l'onglet qu'on a ouvert
// nous-mêmes, avec le `state` annoncé. Une page quelconque qui enverrait l'utilisateur
// sur l'URL Lidl ne déclenche rien (sinon son compte Lidl serait lié à un autre
// compte du portail).

importScripts("lib.js");

const KEY = "pending"; // { [tabId]: { state, origin, portalTabId } } dans storage.session

const getPending = async () => (await chrome.storage.session.get(KEY))[KEY] ?? {};
const setPending = (pending) => chrome.storage.session.set({ [KEY]: pending });

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || msg?.type !== "start" || !sender.tab) return;
  start(msg, sender).then(sendResponse, (e) => sendResponse({ ok: false, error: String(e) }));
  return true; // réponse asynchrone
});

async function start(msg, sender) {
  if (!isValidAuthorizeUrl(msg.url, msg.state)) {
    return { ok: false, error: "URL de connexion Lidl invalide." };
  }
  const tab = await chrome.tabs.create({ url: msg.url, openerTabId: sender.tab.id });
  const pending = await getPending();
  pending[tab.id] = {
    state: msg.state,
    origin: new URL(sender.tab.url).origin,
    portalTabId: sender.tab.id,
  };
  await setPending(pending);
  return { ok: true };
}

// La redirection vers le schéma d'app mobile peut être vue de deux façons selon le
// navigateur : en-tête Location de la réponse, ou événement de redirection.
const FILTER = { urls: ["https://accounts.lidl.com/*"] };
chrome.webRequest.onHeadersReceived.addListener(
  (d) => {
    const location = d.responseHeaders?.find((h) => h.name.toLowerCase() === "location")?.value;
    if (location) handleUrl(d.tabId, location);
  },
  FILTER,
  ["responseHeaders"]
);
chrome.webRequest.onBeforeRedirect.addListener((d) => handleUrl(d.tabId, d.redirectUrl), FILTER);

async function handleUrl(tabId, url) {
  const callback = parseCallback(url);
  if (!callback) return;
  const pending = await getPending();
  const entry = pending[tabId];
  // Onglet inconnu ou state différent : ce n'est pas notre connexion, on ignore.
  if (!entry || entry.state !== callback.state) return;
  delete pending[tabId]; // usage unique (les deux listeners peuvent se déclencher)
  await setPending(pending);

  let result;
  try {
    const res = await fetch(`${entry.origin}/api/lidl/callback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(callback),
      credentials: "omit",
    });
    const body = await res.json().catch(() => ({}));
    result = res.ok ? { ok: true } : { ok: false, error: body.detail || `Erreur ${res.status}` };
  } catch (e) {
    result = { ok: false, error: `Portail injoignable : ${e}` };
  }

  chrome.tabs.remove(tabId).catch(() => {});
  chrome.tabs.update(entry.portalTabId, { active: true }).catch(() => {});
  chrome.tabs.sendMessage(entry.portalTabId, { type: "result", ...result }).catch(() => {});
}

// Onglet fermé avant la fin : on oublie la demande.
chrome.tabs.onRemoved.addListener(async (tabId) => {
  const pending = await getPending();
  if (tabId in pending) {
    delete pending[tabId];
    await setPending(pending);
  }
});
