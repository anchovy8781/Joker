// Run: node test/launcher.test.mjs  (uses jsdom from ../frontend)
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { JSDOM } = require("jsdom");
const html = readFileSync(new URL("../www/index.html", import.meta.url), "utf8")
  .replace(/<script src="launcher.js"><\/script>/, "").replace(/<script src="server-config.js"><\/script>/, "");
const js = readFileSync(new URL("../www/launcher.js", import.meta.url), "utf8");

async function run({ published, saved, manual, builtIn = "https://youtube-exchange.onrender.com", reachable = () => true }) {
  const dom = new JSDOM(html, { url: "https://localhost/", runScripts: "outside-only" });
  const w = dom.window;
  let navigated = null;
  if (saved) w.localStorage.setItem("ytx.server", saved);
  if (manual) w.localStorage.setItem("ytx.manual", "1");
  w.fetch = async (url) => {
    if (url.startsWith("https://api.github.com/")) {
      if (published === undefined) throw new TypeError("offline");
      return { ok: true, json: async () => ({ url: published }) };
    }
    const origin = new URL(url).origin;
    const state = reachable(origin);
    if (state === "notfound") return { ok: false, status: 404, json: async () => { throw new Error("html"); } };
    if (!state) throw new TypeError("unreachable");
    return { ok: true, json: async () => ({ app: "youtube-exchange" }) };
  };
  w.__ytxNavigate = (v) => { navigated = v; };
  w.YTX_DEFAULT_SERVER = builtIn;
  w.eval(js);
  await new Promise((r) => setTimeout(r, 50));
  const doc = w.document;
  return { navigated, setupVisible: !doc.getElementById("setup").hidden, error: doc.getElementById("error").textContent,
           failedVisible: !doc.getElementById("failed").hidden, status: doc.getElementById("status").textContent,
           stored: w.localStorage.getItem("ytx.server") };
}

const assert = (c, m) => { if (!c) { console.error("FAIL:", m); process.exit(1); } else console.log("ok -", m); };

let r = await run({ published: undefined });
assert(r.navigated === "https://youtube-exchange.onrender.com/" && !r.setupVisible, "first launch connects to the built-in server with no input screen");
assert(r.stored === "https://youtube-exchange.onrender.com", "remembers it");

r = await run({ published: "https://youtube-exchange-ab12.onrender.com" });
assert(r.navigated === "https://youtube-exchange-ab12.onrender.com/", "a published address overrides the built-in one");

r = await run({ published: "https://evil.example.com" });
assert(r.navigated === "https://youtube-exchange.onrender.com/", "untrusted published hosts are ignored");

r = await run({ published: undefined, builtIn: null, saved: "https://old-one-two.trycloudflare.com" });
assert(r.navigated === "https://old-one-two.trycloudflare.com/", "falls back to the last working address");

r = await run({ published: undefined, reachable: () => false });
assert(r.navigated === null && !r.setupVisible && r.failedVisible && r.status.includes("연결할 수 없어요"),
       "server down: retry screen, still no address form");

r = await run({ published: undefined, reachable: (o) => o !== "https://youtube-exchange.onrender.com", saved: "https://old-one-two.trycloudflare.com" });
assert(r.navigated === "https://old-one-two.trycloudflare.com/", "built-in down -> tries the last working address");

r = await run({ published: "https://new-a-b.trycloudflare.com", saved: "https://192.168.0.10:3000", manual: true });
assert(r.navigated === "https://192.168.0.10:3000/", "a manually chosen server keeps priority");

r = await run({ published: undefined, reachable: () => "notfound" });
assert(r.navigated === null && r.failedVisible, "host 'Not Found' page (service not deployed) is not treated as the server");
