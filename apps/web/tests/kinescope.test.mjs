// Kinescope as a registered video provider: link rules, and every place the
// provider registry feeds (paste detection, embeds, trims, the player lookup).
import { describe, expect, test } from "bun:test";

const K = await import("../lib/media/kinescope.ts");
const R = await import("../components/Objects/Activities/Video/providers/registry.ts");
const { parseEmbedUrl } = await import("../components/Objects/Editor/Blocks/embeds/embedProviders.ts");

const ID = "9jaV7N5L4H94z89LUtPgoW";
const UUID = "7127f2d7-0e96-40d0-9a03-2e987c096466";

describe("Kinescope links", () => {
  for (const url of [
    `https://kinescope.io/${ID}`,
    `https://kinescope.io/embed/${ID}`,
    `https://www.kinescope.io/${ID}/`,
    `https://kinescope.io/${UUID}`,
    "https://kinescope.io/203613411",
  ]) {
    test(`recognises ${url}`, () => {
      expect(K.kinescopeId(url)).not.toBeNull();
    });
  }

  for (const url of [
    "https://kinescope.io/",
    "https://kinescope.io/embed/",
    "https://kinescope.io/203613411/master.m3u8",
    "https://kinescope.com/203613411",
    "https://evil.example/kinescope.io/abc12",
    "javascript:alert(1)//kinescope.io/abc12",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "",
  ]) {
    test(`rejects ${url || "an empty string"}`, () => {
      expect(K.kinescopeId(url)).toBeNull();
    });
  }

  test("the IFrame Player API gets the share link", () => {
    expect(K.kinescopeVideoUrl(`https://kinescope.io/embed/${ID}?t=30`)).toBe(`https://kinescope.io/${ID}`);
  });

  test("the iframe keeps a numeric start time only", () => {
    expect(K.kinescopeEmbedUrl(`https://kinescope.io/${ID}?t=60`)).toBe(`https://kinescope.io/embed/${ID}?t=60`);
    expect(K.kinescopeEmbedUrl(`https://kinescope.io/${ID}?t=abc`)).toBe(`https://kinescope.io/embed/${ID}`);
  });
});

describe("Kinescope in the provider registry", () => {
  test("is registered with a player", () => {
    const provider = R.findVideoProvider("kinescope");
    expect(provider?.label).toBe("Kinescope");
    expect(provider?.Player).toBeDefined();
    expect(R.isVideoProvider("kinescope")).toBe(true);
    expect(R.videoProviderLabel("kinescope")).toBe("Kinescope");
    expect(R.videoServiceNames()).toContain("Kinescope");
  });

  test("a pasted link is detected, scheme or not", () => {
    expect(R.detectVideoLink(`kinescope.io/${UUID}`)).toMatchObject({
      provider: "kinescope",
      videoId: UUID,
      embedUrl: `https://kinescope.io/embed/${UUID}`,
    });
  });

  test("the built-in services still win", () => {
    expect(R.detectVideoLink("https://youtu.be/aqz-KE-bpKQ")?.provider).toBe("youtube");
  });

  test("an Embed block takes it as a 16:9 video", () => {
    expect(parseEmbedUrl(`https://kinescope.io/${ID}`)).toMatchObject({
      provider: "kinescope",
      embedUrl: `https://kinescope.io/embed/${ID}`,
      aspect: 16 / 9,
    });
  });

  test("a trim reaches the iframe as t", () => {
    expect(R.withTrim(`https://kinescope.io/embed/${ID}`, "kinescope", 42, 90)).toBe(`https://kinescope.io/embed/${ID}?t=42`);
  });
});
