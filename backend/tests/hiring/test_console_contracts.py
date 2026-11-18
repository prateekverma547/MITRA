"""The console's TypeScript copy of the contracts must say what the backend says.

frontend/console/src/contracts.ts mirrors the stage and role values by hand.
A stage added here and not there would render as a blank chip; one renamed
would silently stop counting. This reads the TypeScript and compares.
"""

import re
from pathlib import Path

from shared.stages import CandidateStage, Role

CONTRACTS_TS = Path(__file__).resolve().parents[3] / "frontend" / "console" / "src" / "contracts.ts"


def _const_array(name: str) -> list[str]:
    source = CONTRACTS_TS.read_text()
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.S)
    assert match, f"{name} not found in contracts.ts"
    return re.findall(r'"([a-z_]+)"', match.group(1))


def _record_keys(name: str) -> set[str]:
    source = CONTRACTS_TS.read_text()
    match = re.search(rf"export const {name}: Record<\w+, \w+> = \{{(.*?)\}};", source, re.S)
    assert match, f"{name} not found in contracts.ts"
    return set(re.findall(r"^\s*([a-z_]+):", match.group(1), re.M))


def test_stage_values_match_the_backend_in_order():
    assert _const_array("CANDIDATE_STAGES") == [s.value for s in CandidateStage]


def test_role_values_match_the_backend():
    assert set(_const_array("ROLES")) == {r.value for r in Role}


def test_every_stage_has_a_label_and_a_tone():
    stages = {s.value for s in CandidateStage}
    assert _record_keys("STAGE_LABELS") == stages
    assert _record_keys("STAGE_TONES") == stages
