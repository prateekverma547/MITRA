// Ported from Track I (frontend/candidate-app/tests/proctoring/gaze.test.ts), unchanged but for
// the import paths and this import of the test functions their setup made global.
import { describe, expect, it } from "vitest";
// Sustained versus glance, and the warm-up baseline, on constructed streams.
import { buildBaseline, GazeTracker, isAway, type GazeOutput } from "../../src/features/interview-room/proctoring/gaze";
import { FPS } from "./streams";

const base = { yaw: 2, pitch: -4, eyeH: 0.01, eyeV: 0.03 };

/** Feed a tracker: away during [start, end) intervals, otherwise looking at the screen. */
function run(seconds: number, looks: [number, number][]): GazeOutput[] {
  const g = new GazeTracker();
  const out: GazeOutput[] = [];
  for (let i = 0; i < seconds * FPS; i++) {
    const t = i / FPS;
    out.push(...g.update(t, looks.some(([a, b]) => t >= a && t < b)));
  }
  return out;
}

describe("baseline", () => {
  it("takes the median of each measure, so glances during the warm-up do not move it", () => {
    const samples = [
      ...Array.from({ length: 180 }, () => base),
      ...Array.from({ length: 30 }, () => ({ yaw: 40, pitch: 30, eyeH: 0.3, eyeV: 0.3 })),
    ];
    const b = buildBaseline(samples, 240)!;
    expect(b).toMatchObject(base);
    expect(b.samples).toBe(210);
  });

  it("refuses a baseline when too few warm-up frames showed one face", () => {
    expect(buildBaseline(Array.from({ length: 90 }, () => base), 240)).toBeNull(); // 37.5 %
    expect(buildBaseline(Array.from({ length: 100 }, () => base), 240)).not.toBeNull(); // 41.7 %
    expect(buildBaseline([], 0)).toBeNull();
  });
});

describe("away from the screen, per frame", () => {
  it("compares with the baseline, not with zero", () => {
    expect(isAway(base, base)).toBe(false);
    expect(isAway({ ...base, yaw: base.yaw + 24 }, base)).toBe(false);
    expect(isAway({ ...base, yaw: base.yaw + 26 }, base)).toBe(true);
    expect(isAway({ ...base, pitch: base.pitch + 21 }, base)).toBe(true);
    expect(isAway({ ...base, eyeH: base.eyeH - 0.15 }, base)).toBe(true);
    expect(isAway({ ...base, eyeV: base.eyeV + 0.13 }, base)).toBe(true);
    // A person whose baseline is turned 20 degrees is not "away" at 20 degrees.
    const turned = { ...base, yaw: 20 };
    expect(isAway({ ...base, yaw: 20 }, turned)).toBe(false);
  });
});

describe("sustained versus glance", () => {
  it("ignores a 3-second glance", () => {
    expect(run(30, [[10, 13]])).toEqual([]);
  });

  it("catches a 10-second look: starts when it began, confirmed 6 s in, ends when it ended", () => {
    const out = run(30, [[10, 20]]);
    expect(out[0]).toEqual({ type: "sustained_start", start: 10 });
    expect(out[1].type).toBe("sustained_end");
    expect((out[1] as { end: number }).end).toBeCloseTo(19.875, 3);
    expect(out).toHaveLength(2);
  });

  it("does not split one look on a short return (a blink)", () => {
    // Away 10-14, back for 0.25 s, away 14.25-17: one 7 s look, not two short ones.
    const out = run(30, [
      [10, 14],
      [14.25, 17],
    ]);
    expect(out.map((o) => o.type)).toEqual(["sustained_start", "sustained_end"]);
  });

  it("catches five 2-second looks inside a minute as one repeated event", () => {
    const looks: [number, number][] = [0, 10, 20, 30, 40].map((s) => [5 + s, 7 + s]);
    const out = run(60, looks);
    expect(out).toHaveLength(1);
    expect(out[0]).toMatchObject({ type: "repeated", start: 5, repeats: 5 });
  });

  it("does not count thinking glances: a 2-second look every 20 s for five minutes", () => {
    const looks: [number, number][] = Array.from({ length: 15 }, (_, i) => [10 + i * 20, 12 + i * 20]);
    expect(run(310, looks)).toEqual([]);
  });

  it("does not count short glances however many there are (under 1.5 s each)", () => {
    const looks: [number, number][] = Array.from({ length: 30 }, (_, i) => [5 + i * 3, 6 + i * 3]);
    expect(run(100, looks)).toEqual([]);
  });
});
