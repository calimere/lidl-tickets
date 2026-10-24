// Lancer : node --test extension/
const test = require("node:test");
const assert = require("node:assert/strict");
const { isValidAuthorizeUrl, parseCallback } = require("./lib.js");

const STATE = "a".repeat(43);
const good =
  "https://accounts.lidl.com/connect/authorize?client_id=LidlPlusNativeClient" +
  "&response_type=code&redirect_uri=com.lidlplus.app%3A%2F%2Fcallback" +
  `&state=${STATE}&Country=FR&language=fr-FR`;

test("accepte l'URL d'autorisation Lidl du portail", () => {
  assert.equal(isValidAuthorizeUrl(good, STATE), true);
});

test("refuse une autre origine, un autre chemin ou un autre client", () => {
  assert.equal(isValidAuthorizeUrl(good.replace("accounts.lidl.com", "evil.example"), STATE), false);
  assert.equal(isValidAuthorizeUrl(good.replace("accounts.lidl.com", "accounts.lidl.com.evil.example"), STATE), false);
  assert.equal(isValidAuthorizeUrl(good.replace("/connect/authorize", "/other"), STATE), false);
  assert.equal(isValidAuthorizeUrl(good.replace("LidlPlusNativeClient", "Other"), STATE), false);
  assert.equal(isValidAuthorizeUrl(good.replace("com.lidlplus.app", "evil.app"), STATE), false);
});

test("refuse un state absent, trop court ou différent", () => {
  assert.equal(isValidAuthorizeUrl(good, "autre".repeat(10)), false);
  assert.equal(isValidAuthorizeUrl(good, undefined), false);
  assert.equal(isValidAuthorizeUrl(good.replace(STATE, "court"), "court"), false);
  assert.equal(isValidAuthorizeUrl("pas une url", STATE), false);
});

test("lit code et state de la redirection finale", () => {
  assert.deepEqual(parseCallback(`com.lidlplus.app://callback?code=ABC&state=${STATE}&scope=x`),
    { code: "ABC", state: STATE });
});

test("ignore tout le reste", () => {
  assert.equal(parseCallback("https://accounts.lidl.com/connect/authorize/callback?code=ABC&state=s"), null);
  assert.equal(parseCallback("com.lidlplus.app://callback?error=access_denied&state=s"), null);
  assert.equal(parseCallback("com.lidlplus.app://callback?code=ABC"), null);
  assert.equal(parseCallback(undefined), null);
});
