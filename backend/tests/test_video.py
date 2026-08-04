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

PAGE = (
    Path(__file__).resolve().parents[2] / "frontend" / "candidate" / "index.html"
).read_text()


# -- the room ----------------------------------------------------------------


def test_a_room_starts_with_the_camera_on():
    from bot.services import daily

    assert '"start_video_off": False' in inspect.getsource(daily)


def test_the_bot_does_not_receive_video():
    """Load-bearing, and stated rather than left to a library default.

    A bot that subscribed would decode video for forty minutes inside a
    container whose concurrency cap is already set by unmeasured CPU, and would
    spend it on a picture nothing looks at.
    """
    from bot.services import daily

    assert "video_in_enabled=False" in inspect.getsource(daily)


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
    assert 'id="selfview"' in PAGE
    assert 'id="camera"' in PAGE
    assert "setLocalVideo" in PAGE


def test_there_is_still_no_participant_grid():
    """A self-view is ordinary. A meeting app is not what a candidate should see."""
    assert PAGE.count("<video") == 2, "expected exactly the device-check preview and the self-view"
    assert 'id="orb"' in PAGE and 'id="captions"' in PAGE


# -- what the candidate is told ----------------------------------------------


def test_the_notice_describes_a_recording_that_is_actually_made():
    """This test has now been true in both directions, and that is the point.

    When it was written the notice promised a recording nothing made, so it
    asserted the word was absent. Recording is real as of Phase 2, so the same
    rule now requires the opposite: the notice claims one, and one happens. What
    is being held constant is that the page and the system agree, never a
    particular sentence.
    """
    notice = PAGE[PAGE.index("<h2>Before you begin</h2>") : PAGE.index("I understand and agree")]

    assert "recorded" in notice
    # The vague old wording, which said less than it seemed to.
    assert "recorded and transcribed" not in notice


def test_the_notice_no_longer_says_the_camera_is_unused():
    notice = PAGE[PAGE.index("<h2>Before you begin</h2>") : PAGE.index("I understand and agree")]

    assert "voice only" not in notice
    assert "Your camera is not used" not in notice
    assert "camera and microphone are on" in notice


def test_the_notice_says_what_is_actually_kept():
    """Phase 1 required the notice to say the camera picture was not saved, which
    was true then. Phase 2 saves it, so that sentence had to go and be replaced
    by how long it is kept for."""
    notice = PAGE[PAGE.index("<h2>Before you begin</h2>") : PAGE.index("I understand and agree")]

    assert "transcript" in notice
    assert "Your camera picture is not saved" not in notice
    assert "kept for {{RETENTION_DAYS}} days" in notice
    assert "not scored" in notice


def test_the_device_check_no_longer_says_the_camera_is_switched_off():
    check = PAGE[PAGE.index("Your browser will ask for permission") : PAGE.index('id="grant"')]

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
