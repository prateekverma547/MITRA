"""Which schema revision the code expects, and the errors for when a database is not on it.

The head revision is read from the Alembic scripts rather than typed in here, so
adding a revision cannot leave a stale number behind in the app.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


class SchemaNotMigrated(RuntimeError):
    """The database has tables but no record of which migration produced them."""


class SchemaBehind(RuntimeError):
    """The database is on an older revision than the code needs."""


def head_revision() -> str:
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI))).get_current_head()
