// A self-hosted community build offers SSO; the other Enterprise features stay
// blocked there.
import { describe, expect, mock, test } from "bun:test";

mock.module("@services/config/config", () => ({
  getDeploymentMode: () => "oss",
}));

const { isFeatureAvailable } = await import("../services/plans/plans.ts");

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
