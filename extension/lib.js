// Fonctions pures (testables sous Node) : validation de l'URL d'autorisation Lidl
// et lecture de la redirection finale.

const AUTHORIZE_ORIGIN = "https://accounts.lidl.com";
const AUTHORIZE_PATH = "/connect/authorize";
const CLIENT_ID = "LidlPlusNativeClient";
const REDIRECT_PREFIX = "com.lidlplus.app://callback";

/** L'URL transmise par le portail est-elle bien une demande de connexion Lidl Plus
 *  portant le `state` annoncé ? Évite qu'une page ouvre n'importe quoi. */
function isValidAuthorizeUrl(rawUrl, state) {
  let url;
  try {
    url = new URL(rawUrl);
  } catch {
    return false;
  }
  return (
    url.origin === AUTHORIZE_ORIGIN &&
    url.pathname === AUTHORIZE_PATH &&
    url.searchParams.get("client_id") === CLIENT_ID &&
    url.searchParams.get("redirect_uri") === REDIRECT_PREFIX &&
    typeof state === "string" &&
    state.length >= 16 &&
    url.searchParams.get("state") === state
  );
}

/** Extrait {code, state} d'une URL `com.lidlplus.app://callback?code=...&state=...`,
 *  ou null si ce n'est pas la redirection finale. */
function parseCallback(rawUrl) {
  if (typeof rawUrl !== "string" || !rawUrl.startsWith(REDIRECT_PREFIX)) return null;
  const query = rawUrl.split("?")[1]?.split("#")[0] ?? "";
  const params = new URLSearchParams(query);
  const code = params.get("code");
  const state = params.get("state");
  return code && state ? { code, state } : null;
}

if (typeof module !== "undefined") module.exports = { isValidAuthorizeUrl, parseCallback };
