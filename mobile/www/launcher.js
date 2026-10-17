// App shell launcher: connects to the trading server automatically.
//  1. Reads the address the phone server publishes to GitHub (branch server-address).
//  2. Falls back to the last address that worked.
//  3. Only asks the user when neither is available.
(function () {
  var DISCOVERY = "https://api.github.com/repos/anchovy8781/Joker/contents/server.json?ref=server-address";
  // Only tunnel hosts we actually use are accepted, so a bad published value can't send
  // users to an arbitrary site.
  var TRUSTED = /\.(trycloudflare\.com|ngrok-free\.app|ngrok\.app|ngrok\.io|duckdns\.org)$/;
  var KEY = "ytx.server";
  var MANUAL = "ytx.manual";
  var saved = null, manual = false;
  try { saved = localStorage.getItem(KEY); manual = localStorage.getItem(MANUAL) === "1"; } catch (e) { /* storage unavailable */ }

  var setup = document.getElementById("setup");
  var auto = document.getElementById("auto");
  var status = document.getElementById("status");
  var input = document.getElementById("url");
  var error = document.getElementById("error");

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
    // Opaque (no-cors) response is enough: only network failures matter.
    return fetch(url + "/manifest.webmanifest", { mode: "no-cors", cache: "no-store" });
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

  function start() {
    setup.hidden = true;
    auto.hidden = false;
    status.textContent = "서버에 자동 연결하는 중…";
    // A manually entered address (e.g. a private test server) takes priority until reset.
    var first = manual && saved ? Promise.resolve(saved) : discover();
    first
      .then(function (url) { return tryUrl(url, manual && url === saved); })
      .catch(function () {
        if (saved) {
          status.textContent = "마지막으로 연결한 서버로 접속하는 중…";
          return tryUrl(saved, manual).catch(function () {
            showSetup("서버가 꺼져 있거나 주소가 바뀌었어요. 잠시 후 다시 시도하거나 주소를 입력하세요.");
          });
        }
        showSetup("자동 연결할 서버를 찾지 못했어요. 서버가 켜져 있는지 확인하거나 주소를 입력하세요.");
      });
  }

  start();
})();
