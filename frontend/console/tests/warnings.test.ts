import { describe, expect, it } from "vitest";
import { timesLabel } from "../src/features/interview-room/warnings";

describe("the warning count label", () => {
  it("says which time this is", () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101].map(timesLabel)).toEqual([
      "1st time", "2nd time", "3rd time", "4th time", "11th time", "12th time", "13th time",
      "21st time", "22nd time", "23rd time", "101st time",
    ]);
  });
});
