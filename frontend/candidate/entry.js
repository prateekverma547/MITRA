import { h } from "../shared/dom.js";
import { consentNotice } from "./notice.js";

/**
 * Step 1: the meeting ID and password, then the consent. What the consent says depends on the interview
 * (whether it is recorded), so the credentials are checked first and the notice is shown after. The button
 * stays dead until the box is ticked.
 *
 * `onContinue({ meetingId, password }, recording)` is called once the candidate has ticked the box.
 */
export function renderEntry(config, onContinue) {
  const state = {
    consent: false,
    // null until the credentials have been checked against the server.
    recording: null,
    busy: false,
    error: undefined,
  };

  const meetingInput = h("input", {
    id: "meeting",
    type: "text",
    inputmode: "numeric",
    placeholder: "428-193-756",
    required: true,
    onInput: () => edit(),
  });
  const passwordInput = h("input", {
    id: "password",
    type: "text",
    autocapitalize: "none",
    spellcheck: "false",
    required: true,
    onInput: () => edit(),
  });

  const consentBox = h("input", {
    id: "consent",
    type: "checkbox",
    onChange: (event) => {
      state.consent = event.target.checked;
      refresh();
    },
  });
  const consentLabel = h(
    "label",
    { class: "consent", htmlFor: "consent" },
    consentBox,
    h("span", {}, "I understand and agree to take part on this basis."),
  );

  const errorSlot = h("div", {});
  const consentSlot = h("div", {});
  const submit = h("button", { id: "submit", class: "primary", type: "submit" }, "Check interview");

  // Changing the credentials after a check means the notice may be for another interview.
  function edit() {
    state.recording = null;
    state.consent = false;
    consentBox.checked = false;
    refresh();
  }

  function refresh() {
    submit.disabled = state.busy || (state.recording !== null && !state.consent);
    submit.textContent =
      state.recording === null ? (state.busy ? "Checking…" : "Check interview") : "Continue";
    errorSlot.replaceChildren(
      ...(state.error ? [h("p", { class: "error", role: "alert" }, state.error)] : []),
    );
    consentSlot.replaceChildren(
      ...(state.recording === null ? [] : [consentNotice(config, state.recording, consentLabel)]),
    );
  }

  async function check() {
    state.busy = true;
    state.error = undefined;
    refresh();
    try {
      const response = await fetch("/interviews/notice", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ meeting_id: meetingInput.value, password: passwordInput.value }),
      });
      const body = await response.json();
      if (!response.ok) {
        state.error = body.detail ?? "Could not check the interview.";
        return;
      }
      state.recording = Boolean(body.recording_enabled);
    } catch {
      state.error = "Could not reach the server. Check your connection and try again.";
    } finally {
      state.busy = false;
      refresh();
    }
  }

  const form = h(
    "form",
    {
      id: "form",
      autocomplete: "off",
      onSubmit: (event) => {
        event.preventDefault();
        if (state.recording === null) {
          void check();
          return;
        }
        onContinue({ meetingId: meetingInput.value, password: passwordInput.value }, state.recording);
      },
    },
    h("label", { htmlFor: "meeting" }, "Meeting ID"),
    meetingInput,
    h("label", { htmlFor: "password" }, "Password"),
    passwordInput,
    errorSlot,
    consentSlot,
    submit,
  );

  refresh();

  return h(
    "div",
    { id: "entry-wrap" },
    h("div", { class: "brandline top" }, config.company_credit),
    h(
      "section",
      { class: "card" },
      h("img", { class: "brandmark", src: config.logo_url, alt: config.bot_name }),
      h("h1", {}, "Join your interview"),
      h(
        "p",
        { class: "sub" },
        "You will be interviewed by ",
        h("strong", {}, config.bot_name),
        `. ${config.product_tagline}`,
      ),
      form,
    ),
    h("div", { class: "brandline foot" }, config.company_name),
  );
}
