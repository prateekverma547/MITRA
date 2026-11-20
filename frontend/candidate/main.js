import { loadConfig } from "../shared/config.js";
import { h } from "../shared/dom.js";
import { renderEntry } from "./entry.js";
import { renderDeviceCheck } from "./device-check.js";
import { renderStage } from "./stage.js";

/**
 * The candidate's journey: credentials and consent, then the device check, then the call, then a
 * thank-you. Each step is a screen, and the screen only moves forward once the step is done.
 */
async function start() {
  const app = document.getElementById("app");
  let config;
  try {
    config = await loadConfig();
  } catch (err) {
    app.replaceChildren(
      h("section", { class: "card", style: { margin: "40px auto", maxWidth: "520px" } },
        h("h1", {}, "This page could not start"),
        h("p", { class: "sub" }, err.message),
      ),
    );
    return;
  }
  document.title = `Join your interview · ${config.bot_name}`;

  const show = (node) => app.replaceChildren(node);

  let credentials = { meetingId: "", password: "" };
  let recording = false;

  const showEntry = () => show(renderEntry(config, (creds, recorded) => {
    credentials = creds;
    recording = recorded;
    showCheck();
  }));

  const showCheck = () => {
    const check = renderDeviceCheck(config, {
      interviewRecorded: recording,
      join: joinInterview,
      onJoined: (session) => showStage(session),
    });
    show(check.element);
  };

  const showStage = (session) => {
    show(
      renderStage(config, {
        session,
        credentials,
        onEnded: (note) => showDone(note),
      }),
    );
  };

  const showDone = (note) => {
    show(
      h(
        "section",
        { id: "done" },
        h("div", { class: "brandline top" }, config.company_credit),
        h(
          "div",
          {},
          h("img", { class: "brandmark", src: config.logo_url, alt: config.bot_name }),
          h("h1", {}, "Thank you for your time"),
          h("p", { class: "sub", id: "done-note" }, note ?? "Your interview has ended. You can close this tab."),
        ),
        h("div", { class: "brandline foot" }, config.company_name),
      ),
    );
  };

  // Asked for only once the device check passes, so the room is booked only when the candidate is ready.
  // The browser's own timezone is the only thing that knows what time it is where the candidate sits;
  // the server runs on UTC, so without this the bot would greet them by the wrong clock.
  async function joinInterview() {
    try {
      const response = await fetch("/interviews/join", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          meeting_id: credentials.meetingId,
          password: credentials.password,
          consent: true,
          timezone: browserTimezone(),
        }),
      });
      const body = await response.json();
      if (!response.ok) return { error: body.detail ?? "Could not join the interview." };
      return { session: body };
    } catch {
      return { error: "Could not reach the server. Check your connection and try again." };
    }
  }

  showEntry();
}

function browserTimezone() {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null; // never block a join over a greeting
  }
}

void start();
