// App shell launcher: connects to the trading server automatically — no address screen.
//  1. An address published to GitHub (branch server-address) overrides everything.
//  2. Otherwise the built-in cloud server (server-config.js).
//  3. Otherwise the last address that worked.
// The manual form is only reachable from the error screen ("다른 서버").
(function () {
  var DISCOVERY = "https://api.github.com/repos/anchovy8781/Joker/contents/server.json?ref=server-address";
  // Only tunnel hosts we actually use are accepted, so a bad published value can't send
  // users to an arbitrary site.
  var TRUSTED = /\.(onrender\.com|trycloudflare\.com|ngrok-free\.app|ngrok\.app|ngrok\.io|duckdns\.org)$/;
  var BUILT_IN = window.YTX_DEFAULT_SERVER || null;
  var KEY = "ytx.server";
  var MANUAL = "ytx.manual";
  var saved = null, manual = false;
  try { saved = localStorage.getItem(KEY); manual = localStorage.getItem(MANUAL) === "1"; } catch (e) { /* storage unavailable */ }

  var setup = document.getElementById("setup");
  var auto = document.getElementById("auto");
  var status = document.getElementById("status");
  var input = document.getElementById("url");
  var error = document.getElementById("error");
  var failed = document.getElementById("failed");
  var spinner = document.querySelector(".spinner");

  function normalise(v, requireTrusted) {
    v = (v || "").trim().replace(/\/+$/, "");
    if (v && !/^https?:\/\//i.test(v)) v = "https://" + v;
    try {
      var u = new URL(v);
      var lan = /^(localhost|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(u.hostname);
      if (u.protocol !== "https:" && !lan) return null;
      if (requireTrusted && !TRUSTED.test(u.hostname)) return null;
      return u.origin;
    } catch (e) { return null; }
  }

  function remember(url, isManual) {
    try { localStorage.setItem(KEY, url); localStorage.setItem(MANUAL, isManual ? "1" : "0"); } catch (e) { /* ignore */ }
  }

  function reachable(url) {
    // Must be this app's server, not a host's "Not Found" page for a service that doesn't exist.
    return fetch(url + "/api/ping", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (d) { if (!d || d.app !== "youtube-exchange") throw new Error("not ytx"); });
  }

  // window.__ytxNavigate exists only in the launcher tests (jsdom cannot navigate).
  function go(url) { (window.__ytxNavigate || function (u) { window.location.href = u; })(url + "/"); }

  function showSetup(message) {
    auto.hidden = true;
    setup.hidden = false;
    input.value = saved || "";
    error.textContent = message || "";
  }

  function discover() {
    return fetch(DISCOVERY, { headers: { Accept: "application/vnd.github.raw+json" }, cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (data) {
        var url = normalise(data && data.url, true);
        if (!url) throw new Error("invalid address");
        return url;
      });
  }

  function tryUrl(url, isManual) {
    return reachable(url).then(function () { remember(url, isManual); go(url); });
  }

  document.getElementById("change").addEventListener("click", function () { showSetup(); });
  document.getElementById("retry").addEventListener("click", function () { start(); });

  setup.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var url = normalise(input.value, false);
    if (!url) { error.textContent = "올바른 https 주소를 입력하세요."; return; }
    error.textContent = "연결 확인 중…";
    tryUrl(url, true).catch(function () { error.textContent = "서버에 연결할 수 없습니다. 주소와 서버 실행 상태를 확인하세요."; });
  });

  document.getElementById("auto-retry").addEventListener("click", function () {
    try { localStorage.setItem(MANUAL, "0"); } catch (e) { /* ignore */ }
    start();
  });

  function showFailure() {
    setup.hidden = true;
    auto.hidden = false;
    failed.hidden = false;
    spinner.hidden = true;
    status.textContent = "서버에 연결할 수 없어요. 서버가 아직 준비되지 않았거나 인터넷 연결이 끊겼어요. 잠시 후 다시 시도해 주세요.";
  }

  function start() {
    setup.hidden = true;
    auto.hidden = false;
    failed.hidden = true;
    spinner.hidden = false;
    status.textContent = "서버에 연결하는 중…";
    // A manually entered address (e.g. a private test server) takes priority until reset.
    var candidates = manual && saved ? [Promise.resolve(saved)] : [];
    candidates.push(discover());
    if (BUILT_IN) candidates.push(Promise.resolve(normalise(BUILT_IN, true)));
    if (saved) candidates.push(Promise.resolve(saved));
    var i = 0;
    (function next() {
      if (i >= candidates.length) return showFailure();
      candidates[i++]
        .then(function (url) {
          if (!url) throw new Error("none");
          return tryUrl(url, manual && url === saved);
        })
        .catch(next);
    })();
  }

  start();
})();
