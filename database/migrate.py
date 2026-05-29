"""Programmatic Alembic upgrade used to self-heal the schema on boot.

Deploying new application code without running ``alembic upgrade head`` on
the target database leaves the schema behind the models (e.g. a missing
``words.frequency_score`` column), which surfaces as HTTP 500s. Running the
upgrade on startup keeps the live database in sync with the deployed code.
The call is a no-op when the database is already at ``head``.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config

from utils.logger import get_logger

log = get_logger(__name__)

_ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"
_ALEMBIC_INI = _ALEMBIC_DIR / "alembic.ini"


def _build_config() -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    # Use absolute paths so the upgrade works regardless of the process CWD.
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    return cfg


def upgrade_to_head() -> None:
    """Apply any pending migrations. Raises on failure."""
    command.upgrade(_build_config(), "head")
