// Platform health check: answered by the web server alone, so a busy API never makes the
// host think the service is dead and restart it (a restart shows visitors 502 errors).
export const dynamic = "force-static";

export function GET() {
  return new Response("ok", { headers: { "Content-Type": "text/plain" } });
}
