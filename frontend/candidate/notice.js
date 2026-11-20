import { h } from "../shared/dom.js";

// The consent copy. It has to be true on the day it is read, so the recording version follows the
// server's setting, and the server decides which one is true. Both versions live side by side here
// so the next person who flips the switch can compare them. Edit both, or a candidate is told
// something that is not happening.

/** What the candidate reads before they agree. `extra` is appended after the list (the consent box). */
export function consentNotice(config, recording, ...extra) {
  return h(
    "div",
    { class: "notice" },
    h("h2", {}, "Before you begin"),
    h(
      "ul",
      {},
      h(
        "li",
        {},
        "This interview is conducted by ",
        h("strong", {}, config.bot_name),
        `, an AI interviewer (${config.bot_full_name}), not a person.`,
      ),
      recording
        ? [
            h(
              "li",
              {},
              "Your camera and microphone are on, and the interview is ",
              h("strong", {}, "recorded, both video and sound"),
              ". What you say is also written down as a transcript.",
            ),
            h(
              "li",
              {},
              "The recording and the transcript are shared with the employer, along with a report based on the transcript. ",
              h("strong", {}, "Someone there may watch the recording."),
            ),
            h(
              "li",
              {},
              "The recording is ",
              h("strong", {}, `kept for ${config.retention_days} days`),
              " and is then deleted. The transcript is kept.",
            ),
            h(
              "li",
              {},
              "You are assessed on what you say. Your camera picture is not scored, and nothing measures your face, your eyes or your expression.",
            ),
          ]
        : [
            h(
              "li",
              {},
              "Your camera and microphone are on for the interview. What you say is ",
              h("strong", {}, "written down as a transcript"),
              ", and the transcript is kept.",
            ),
            h(
              "li",
              {},
              h("strong", {}, "The interview is not recorded."),
              " No video or sound from it is saved, and there is nothing for anyone to watch back.",
            ),
            h(
              "li",
              {},
              "The transcript is shared with the employer, along with a report based on it, to help them consider your application.",
            ),
            h(
              "li",
              {},
              "You are assessed on what you say. Your camera picture is not scored, and nothing measures your face, your eyes or your expression.",
            ),
          ],
      h(
        "li",
        {},
        "If you move to another tab or window, we record that the interview page was not in view, and for how long. ",
        h("strong", {}, "We cannot see what you were looking at."),
        " This is not scored, and it is shown to the person reviewing your interview.",
      ),
      h("li", {}, "A person reviews the result. Nothing here decides the outcome on its own."),
    ),
    ...extra,
  );
}

/** What the candidate reads on the device check, before the browser asks for permission. */
export function deviceNotice(recording) {
  return h(
    "div",
    { class: "notice" },
    h("h2", {}, "Your browser will ask for permission"),
    h(
      "ul",
      {},
      h("li", {}, "Your ", h("strong", {}, "microphone"), " is needed. The interview is a spoken conversation."),
      recording
        ? [
            h(
              "li",
              {},
              "Your ",
              h("strong", {}, "camera"),
              " is on and recorded during the interview, so check it here first. You can turn it off at any point once you are in.",
            ),
            h("li", {}, "This check itself is not recorded, and nothing leaves your device until you join."),
          ]
        : [
            h(
              "li",
              {},
              "Your ",
              h("strong", {}, "camera"),
              " is on during the interview, so check it here first. You can turn it off at any point once you are in.",
            ),
            h("li", {}, "Nothing on this screen leaves your device."),
          ],
    ),
  );
}
