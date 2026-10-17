// Run: node test/launcher.test.mjs  (uses jsdom from ../frontend)
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
const require = createRequire(new URL("../../frontend/package.json", import.meta.url));
const { JSDOM } = require("jsdom");
const html = readFileSync(new URL("../www/index.html", import.meta.url), "utf8").replace(/<script src="launcher.js"><\/script>/, "");
const js = readFileSync(new URL("../www/launcher.js", import.meta.url), "utf8");

async function run({ published, saved, manual, reachable = () => true }) {
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
    if (!reachable(origin)) throw new TypeError("unreachable");
    return { ok: true };
  };
  w.__ytxNavigate = (v) => { navigated = v; };
  w.eval(js);
  await new Promise((r) => setTimeout(r, 50));
  const doc = w.document;
  return { navigated, setupVisible: !doc.getElementById("setup").hidden, error: doc.getElementById("error").textContent,
           stored: w.localStorage.getItem("ytx.server") };
}

const assert = (c, m) => { if (!c) { console.error("FAIL:", m); process.exit(1); } else console.log("ok -", m); };

let r = await run({ published: "https://tall-river-sun-bay.trycloudflare.com" });
assert(r.navigated === "https://tall-river-sun-bay.trycloudflare.com/" && !r.setupVisible, "auto-connects to the published address with no input");
assert(r.stored === "https://tall-river-sun-bay.trycloudflare.com", "remembers it");

r = await run({ published: "https://evil.example.com" });
assert(r.navigated === null && r.setupVisible, "rejects untrusted published hosts");

r = await run({ published: undefined, saved: "https://old-one-two.trycloudflare.com" });
assert(r.navigated === "https://old-one-two.trycloudflare.com/", "falls back to the last address when discovery fails");

r = await run({ published: "https://new-a-b.trycloudflare.com", saved: "https://old-one-two.trycloudflare.com" });
assert(r.navigated === "https://new-a-b.trycloudflare.com/", "a newly published address replaces the old one");

r = await run({ published: "https://down-a-b.trycloudflare.com", reachable: () => false });
assert(r.navigated === null && r.setupVisible && r.error.includes("서버"), "shows a clear message when the server is off");

r = await run({ published: "https://new-a-b.trycloudflare.com", saved: "https://192.168.0.10:3000", manual: true });
assert(r.navigated === "https://192.168.0.10:3000/", "a manually chosen server keeps priority");
