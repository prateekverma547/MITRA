// Small pieces of console logic that every screen leans on.
import { describe, expect, it } from "vitest";
import { requestPhase } from "../src/contracts";
import { clock, initials, isMitra, type Peer } from "../src/features/interview-room/media";
import { waitClass } from "../src/shell/ui/waitClass";

describe("how long someone has waited, as a notification colour", () => {
  it.each([
    [0, "wait fresh"],
    [0.9, "wait fresh"],
    [1, "wait soon"],
    [2.5, "wait soon"],
    [3, "wait long"],
    [10, "wait long"],
  ])("%s days is %s", (days, cls) => {
    expect(waitClass(days)).toBe(cls);
  });
});

describe("where a job request stands", () => {
  it("a draft is with the BU head until its description is ready", () => {
    expect(requestPhase({ request_status: "draft", spec_status: "awaiting_clarification" }).label).toBe("Being described");
    expect(requestPhase({ request_status: "draft", spec_status: "ready" }).label).toBe("Ready to approve");
  });
  it("an open request is with HR, and a closed one says so", () => {
    expect(requestPhase({ request_status: "open", spec_status: "ready" })).toEqual({ label: "With HR", tone: "good" });
    expect(requestPhase({ request_status: "closed", spec_status: "ready" }).label).toBe("Closed");
  });
});

describe("the candidate's room", () => {
  const peer = (name: string): Peer => ({ id: "p", name, local: false, micOn: true, camOn: false, camBlocked: false, micBlocked: false });

  it("recognises MITRA by the Daily name the interviewer joins with", () => {
    expect(isMitra(peer("Interviewer"), "Mitra")).toBe(true);
    expect(isMitra(peer(" mitra "), "Mitra")).toBe(true);
    expect(isMitra(peer("Priya Nair"), "Mitra")).toBe(false);
  });

  it("shows the clock as minutes and seconds, with hours only when needed", () => {
    expect(clock(0)).toBe("0:00");
    expect(clock(65)).toBe("1:05");
    expect(clock(3725)).toBe("1:02:05");
    expect(clock(-4)).toBe("0:00");
  });

  it("makes initials from a name", () => {
    expect(initials("Priya Nair")).toBe("PN");
    expect(initials("asha")).toBe("A");
    expect(initials("  ")).toBe("?");
  });
});
