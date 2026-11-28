// A self-hosted community build offers SSO; the other Enterprise features stay
// blocked there.
import { describe, expect, mock, test } from "bun:test";

mock.module("@services/config/config", () => ({
  getDeploymentMode: () => "oss",
  getAPIUrl: () => "https://lms.example.test/api/v1/",
}));

const { isFeatureAvailable } = await import("../services/plans/plans.ts");
const { initiateSSOLogin } = await import("../services/auth/sso.ts");

describe("SSO in the community edition", () => {
  test("is available", () => {
    expect(isFeatureAvailable("sso")).toBe(true);
  });

  test("leaves the Enterprise-only features blocked", () => {
    for (const key of ["lti", "scorm", "audit_logs", "payments"]) {
      expect(isFeatureAvailable(key)).toBe(false);
    }
  });
});

describe("starting an SSO sign-in", () => {
  test("carries the page to come back to", async () => {
    const calls = [];
    globalThis.fetch = async (url, init) => {
      calls.push({ url: String(url), init });
      return new Response(JSON.stringify({ authorization_url: "https://idp.example.test/a", state: "s" }), { status: 200 });
    };
    await initiateSSOLogin("astrateam", "/oauth/consent?client_id=c1");
    const url = new URL(calls[0].url);
    expect(url.pathname).toBe("/api/v1/auth/sso/authorize");
    expect(url.searchParams.get("org_slug")).toBe("astrateam");
    expect(url.searchParams.get("next")).toBe("/oauth/consent?client_id=c1");
    expect(calls[0].init.credentials).toBe("include");
  });
});
