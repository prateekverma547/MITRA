"""The candidate's camera is on, and what that is allowed to cost.

Video exists so a person can review an interview afterwards. It is never scored,
and nothing in this system watches it live: the interviewer works from the
transcript. So the whole of this change is about publishing a modest stream
without touching the thing the assessment actually rests on, which is audio.

The page itself has no executable coverage and there is no JS harness, so the
browser side is asserted by reading the file, the way `test_copy_style.py`
already checks its copy.
"""

import inspect
from pathlib import Path

import pytest

from tests.frontend_source import consent_notice, device_notice, join_source  # noqa: E402

# The candidate's page is plain JavaScript. Its behaviour is in the join source, which these tests read.
PAGE = join_source()


# -- the room ----------------------------------------------------------------


def test_a_room_starts_with_the_camera_on():
    from bot.services import daily

    assert '"start_video_off": False' in inspect.getsource(daily)


def test_the_bot_receives_video_only_when_the_camera_check_is_on():
    """Load-bearing, and stated rather than left to a library default.

    Off by default: a bot that subscribed would decode video for forty minutes
    inside a container whose concurrency cap is already set by unmeasured CPU.
    On only for the camera hearing check, which reads the candidate's lips to
    decide whether unheard speech was real (docs/DECISIONS.md).
    """
    from bot.services import daily

    sig = inspect.signature(daily.build_transport)
    assert sig.parameters["camera_hearing"].default is False
    assert "video_in_enabled=camera_hearing" in inspect.getsource(daily)


def test_the_bot_still_publishes_no_video():
    """The interviewer is a voice. There is no avatar and no empty camera track."""
    from bot.services import daily

    assert "camera_out_enabled=False" in inspect.getsource(daily)


def test_audio_is_untouched():
    """Turning video on must not have altered how audio is carried."""
    from bot.services import daily

    source = inspect.getsource(daily)
    assert "audio_in_enabled=True" in source
    assert "audio_out_enabled=True" in source
    assert '"start_audio_off": False' in source


# -- what the browser sends --------------------------------------------------


def test_the_page_turns_the_camera_on():
    assert "videoSource: true" in PAGE
    assert "videoSource: false" not in PAGE


def test_the_camera_is_capped_at_capture():
    """Not left at whatever the webcam offers. This is a talking head."""
    assert "userMediaVideoConstraints" in PAGE
    assert "max: 640" in PAGE
    assert "max: 480" in PAGE
    assert "max: 15" in PAGE


def test_only_one_send_layer_is_asked_for():
    """Simulcast serves subscribers at different qualities and there are no
    video subscribers at all, so a ladder would pay several times over for a
    picture nothing watches."""
    assert "sendSettings" in PAGE
    assert "maxBitrate: 200000" in PAGE


def test_the_candidate_can_see_and_stop_their_own_camera():
    """A camera you cannot see is worse than no camera, and one you cannot turn
    off in your own home is worse still. Video is not scored, so switching it
    off cannot change an assessment."""
    assert '"selfview"' in PAGE
    assert '"camera"' in PAGE
    assert "setLocalVideo" in PAGE


def test_there_is_still_no_participant_grid():
    """A self-view is ordinary. A meeting app is not what a candidate should see."""
    assert PAGE.count('h("video"') == 2, "expected exactly the device-check preview and the self-view"
    assert '"orb"' in PAGE and '"captions"' in PAGE


# -- what the candidate is told ----------------------------------------------


def test_the_notice_describes_whatever_is_actually_happening():
    """This test has now been true in three directions, and that is the point.

    First it asserted the notice must not mention a recording, because none was
    made. Then recording shipped and it required the opposite. Now recording is
    behind a switch that is off by default, so the assertion is about the page
    tracking the switch rather than about any particular sentence. What is held
    constant throughout is that the page and the system agree.

    It reads the served page rather than the file, because the file now carries
    both versions and reading it proves nothing about what a candidate sees. The
    detail lives in `test_recordings.py`; this is the marker left where the last
    two versions of the claim were made.
    """
    assert "not recorded" in _notice_now()


def _notice_now() -> str:
    """The notice a candidate reads with recording off, the setting in force by default."""
    return consent_notice(recording=False)


def test_the_notice_no_longer_says_the_camera_is_unused():
    """The camera stayed on when recording went off, so this holds either way."""
    notice = _notice_now()

    assert "voice only" not in notice
    assert "Your camera is not used" not in notice
    assert "camera and microphone are on" in notice


def test_the_notice_says_what_is_actually_kept():
    """Phase 1 said the camera picture was not saved. Phase 2 saved it and gave
    a retention period instead. With recording switched off the transcript is
    the only thing kept, and that is what it now says."""
    notice = _notice_now()

    assert "transcript is kept" in notice
    assert "No video or sound from" in notice
    assert "not scored" in notice


def test_the_device_check_no_longer_says_the_camera_is_switched_off():
    check = device_notice(recording=False)

    assert "switched off again before you join" not in check
    assert "voice only" not in check
    assert "during the interview" in check


def test_no_dashes_in_the_new_copy():
    """PROSE_STYLE, the same rule test_copy_style.py enforces on this page."""
    for dash in ("—", "–"):
        offending = [
            line.strip()
            for line in PAGE.splitlines()
            if dash in line and not line.strip().startswith("//")
        ]
        assert not offending, offending
