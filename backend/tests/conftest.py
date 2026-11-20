"""Suite-wide test settings.

The drop detector's background loop (live/drops.py) is off for the suite: the
status board is shared by every test in the process, and a loop acting on
another test's readings would make results depend on timing. Its tests drive
`DropDetector.tick` directly with simulated readings and times.
"""

import os

os.environ.setdefault("DROP_DETECTOR_DISABLED", "1")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _booking_preparation_offline(monkeypatch):
    """Every booking runs Session A's refine_for_booking (C-A23). In the suite its one
    text-model call is a stand-in (tests/booking_stand_in.py), so no booking ever reaches
    OpenAI. A test that needs another behaviour patches `refinement_function` itself."""
    from live import hr_hook
    from tests.booking_stand_in import booking_function, stand_in

    monkeypatch.setattr(hr_hook, "refinement_function", lambda: booking_function(stand_in()))
