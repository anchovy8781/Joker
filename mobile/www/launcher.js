// App shell launcher: remembers the trading server address and opens it in the app's WebView.
(function () {
  var KEY = "ytx.server";
  var saved = null;
  try { saved = localStorage.getItem(KEY); } catch (e) { /* storage unavailable */ }
  var setup = document.getElementById("setup");
  var auto = document.getElementById("auto");
  var input = document.getElementById("url");
  var error = document.getElementById("error");
  var timer = null;

  function normalise(v) {
    v = (v || "").trim().replace(/\/+$/, "");
    if (v && !/^https?:\/\//i.test(v)) v = "https://" + v;
    try {
      var u = new URL(v);
      // Real servers must use HTTPS (session cookies are Secure). http is allowed only for LAN testing.
      if (u.protocol !== "https:" && !/^(localhost|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)/.test(u.hostname)) return null;
      return u.origin;
    } catch (e) { return null; }
  }

  function go(url) { window.location.href = url + "/"; }

  function showSetup() {
    if (timer) clearTimeout(timer);
    auto.hidden = true;
    setup.hidden = false;
    input.value = saved || "";
    input.focus();
  }

  document.getElementById("change").addEventListener("click", showSetup);

  setup.addEventListener("submit", function (ev) {
    ev.preventDefault();
    var url = normalise(input.value);
    if (!url) { error.textContent = "올바른 https 주소를 입력하세요."; return; }
    error.textContent = "연결 확인 중…";
    // Reachability check (opaque response is fine; only network failures matter).
    fetch(url + "/manifest.webmanifest", { mode: "no-cors", cache: "no-store" })
      .then(function () {
        try { localStorage.setItem(KEY, url); } catch (e) { /* ignore */ }
        go(url);
      })
      .catch(function () { error.textContent = "서버에 연결할 수 없습니다. 주소와 서버 실행 상태를 확인하세요."; });
  });

  if (saved) {
    document.getElementById("saved-url").textContent = saved;
    auto.hidden = false;
    timer = setTimeout(function () { go(saved); }, 1200);  // time to tap "서버 주소 변경"
  } else {
    showSetup();
  }
})();
