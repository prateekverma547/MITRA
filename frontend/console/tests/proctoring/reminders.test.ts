// Ported from Track I (frontend/candidate-app/tests/proctoring/reminders.test.ts), unchanged but for
// the import paths and this import of the test functions their setup made global.
import { describe, expect, it } from "vitest";
// The reminder policy (task 16): cap, cooldown, log-only, technical, accommodation.
import { REMINDER_COOLDOWN_S, REMINDERS_PER_KIND } from "../../src/features/interview-room/proctoring/thresholds";
import { reminderMessage, ReminderPolicy } from "../../src/features/interview-room/proctoring/reminders";

describe("reminder policy", () => {
  it("allows three per kind, then records silently", () => {
    const p = new ReminderPolicy({ gazeRemindersOff: false });
    const d = [0, 200, 400, 600, 800].map((t) => p.decide("distance_out_of_band", t));
    expect(d.map((x) => x.reminder)).toEqual(["nudged", "nudged", "nudged", "recorded_only", "recorded_only"]);
    expect(d.map((x) => x.reminder_index)).toEqual([1, 2, 3, null, null]);
    expect(d[3].why).toBe("cap");
    expect(REMINDERS_PER_KIND).toBe(3);
  });

  it("waits two minutes between two of the same kind", () => {
    const p = new ReminderPolicy({ gazeRemindersOff: false });
    // Track H: second_face here, as face_missing is now a technical prompt (uncapped).
    expect(p.decide("second_face", 100).reminder).toBe("nudged");
    const soon = p.decide("second_face", 100 + REMINDER_COOLDOWN_S - 1);
    expect(soon).toMatchObject({ reminder: "recorded_only", why: "cooldown" });
    expect(p.decide("second_face", 100 + REMINDER_COOLDOWN_S)).toMatchObject({ reminder: "nudged", reminder_index: 2 });
    expect(REMINDER_COOLDOWN_S).toBe(120);
  });

  it("counts each kind on its own", () => {
    const p = new ReminderPolicy({ gazeRemindersOff: false });
    expect(p.decide("looking_away", 10).reminder_index).toBe(1);
    expect(p.decide("distance_out_of_band", 11).reminder_index).toBe(1);
    expect(p.decide("second_face", 12).reminder_index).toBe(1);
  });

  it("a face out of view is a technical prompt: shown every time, never capped (Track H, 2026-11-20)", () => {
    const p = new ReminderPolicy({ gazeRemindersOff: false });
    for (let i = 0; i < 6; i++)
      expect(p.decide("face_missing", i * 10)).toMatchObject({ reminder: "technical_prompt", reminder_index: null, technicalPrompt: true });
  });

  it("never shows leaving full screen, paste or a second monitor", () => {
    const p = new ReminderPolicy({ gazeRemindersOff: false });
    for (const kind of ["left_full_screen", "paste", "second_monitor"] as const)
      expect(p.decide(kind, 0)).toMatchObject({ reminder: "recorded_only", reminder_index: null, why: "log_only" });
  });

  it("always shows the share prompt and never counts it against a cap", () => {
    const p = new ReminderPolicy({ gazeRemindersOff: false });
    const d = Array.from({ length: 6 }, (_, i) => p.decide("share_stopped", i));
    expect(d.every((x) => x.technicalPrompt && x.reminder === "technical_prompt" && x.reminder_index === null && x.why === "technical")).toBe(true);
    // Not counted against the share kinds' cap, and nothing else is a technical prompt.
    expect(p.shownCount("share_stopped")).toBe(0);
    expect(p.decide("tab_hidden", 0).reminder).toBe("nudged");
  });

  it("with the accommodation, never nudges gaze but still nudges everything else", () => {
    const p = new ReminderPolicy({ gazeRemindersOff: true });
    expect(p.decide("looking_away", 0)).toMatchObject({ reminder: "recorded_only", why: "accommodation" });
    expect(p.decide("looking_away", 500)).toMatchObject({ reminder: "recorded_only", why: "accommodation" });
    expect(p.decide("distance_out_of_band", 0).reminder).toBe("nudged");
  });

  it("words reminders as DEV_PLAN section 7 does", () => {
    expect(reminderMessage("distance_out_of_band", "near")).toBe("Please sit back a little");
    expect(reminderMessage("distance_out_of_band", "far")).toBe("Please come a little closer");
    expect(reminderMessage("looking_away")).toBe("Please keep your eyes on the screen");
    expect(reminderMessage("face_missing")).toBe("We can't see you clearly");
    expect(reminderMessage("second_face")).toBe("Please make sure only you are in view.");
    expect(reminderMessage("tab_unfocused")).toBe("Please return to the interview tab");
    expect(reminderMessage("paste")).toBeNull();
  });
});
