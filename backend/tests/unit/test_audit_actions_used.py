"""Every action in the audit catalogue (PROJECT_CONTEXT section 9) must be written somewhere in the app."""
import re
from pathlib import Path

from app.services import audit_service

APP_DIR = Path(audit_service.__file__).resolve().parents[1]
# Actions whose step is not built yet. Empty since BE-7: every action is written somewhere.
NOT_BUILT_YET: set[str] = set()


def _source_without_the_catalogue() -> str:
    parts = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if "engine" in path.relative_to(APP_DIR).parts or path.name == "audit_service.py":
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_every_catalogue_action_fires_somewhere():
    source = _source_without_the_catalogue()
    used = {action for action in audit_service.ACTIONS if re.search(rf'"{action}"', source)}
    assert audit_service.ACTIONS - used - NOT_BUILT_YET == set()
    assert used & NOT_BUILT_YET == set(), "these are built now - remove them from NOT_BUILT_YET"
