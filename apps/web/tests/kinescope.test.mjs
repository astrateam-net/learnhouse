import { describe, expect, test } from "bun:test";

import { isKinescopeUrl, kinescopeEmbedUrl, kinescopeVideoUrl } from "../lib/media/kinescope.ts";

const ID = "9jaV7N5L4H94z89LUtPgoW";

describe("Kinescope links", () => {
  for (const url of [
    `https://kinescope.io/${ID}`,
    `https://kinescope.io/embed/${ID}`,
    `https://www.kinescope.io/${ID}/`,
    "https://kinescope.io/203613411",
  ]) {
    test(`recognises ${url}`, () => {
      expect(isKinescopeUrl(url)).toBe(true);
    });
  }

  for (const url of [
    "https://kinescope.io/",
    "https://kinescope.io/203613411/master.m3u8",
    "https://evil.example/kinescope.io/abc",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "",
  ]) {
    test(`rejects ${url || "an empty string"}`, () => {
      expect(isKinescopeUrl(url)).toBe(false);
    });
  }

  test("the IFrame Player API gets the share link", () => {
    expect(kinescopeVideoUrl(`https://kinescope.io/embed/${ID}?t=30`)).toBe(`https://kinescope.io/${ID}`);
  });

  test("the iframe gets the embed link with its player parameters", () => {
    expect(kinescopeEmbedUrl(`https://kinescope.io/${ID}?seek=60&duration=30#x`)).toBe(
      `https://kinescope.io/embed/${ID}?seek=60&duration=30`,
    );
  });
});
