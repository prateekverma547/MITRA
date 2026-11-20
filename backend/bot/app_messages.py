"""App messages the bot accepts from the call (joint A + B step).

The candidate app's End button, once confirmed in its dialog, sends Mitra
(frontend/candidate-app/src/call/dailyCall.ts:50, :104-113):

    {"label": "mitra-candidate", "type": "end-interview", "reason": "end_button"}

It is accepted ONLY from this interview's candidate: the sender's Daily session
id must be a participant the bot can see whose user_id is
"candidate:<interview id>" (bot/presence.candidate_user_id). Anyone else, an
unknown sender (a hidden watcher is not in participants(), C-53/C-54), or a
malformed message is ignored.
"""

from bot.presence import candidate_user_id, participant_user_id

END_REQUEST_LABEL = "mitra-candidate"
END_REQUEST_TYPE = "end-interview"


def is_end_request(message) -> bool:
    return (isinstance(message, dict) and message.get("label") == END_REQUEST_LABEL
            and message.get("type") == END_REQUEST_TYPE)


def is_candidate_end_request(message, sender, participants, interview_id: str | None) -> bool:
    """True only for the End request sent by this interview's candidate."""
    if not interview_id or not is_end_request(message) or not isinstance(sender, str):
        return False
    try:
        participant = (participants or {}).get(sender)
    except Exception:  # noqa: BLE001  (a transport that cannot list participants)
        return False
    if not participant:
        return False
    return participant_user_id(participant) == candidate_user_id(interview_id)
