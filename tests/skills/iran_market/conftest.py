"""Make the iran-market-analysis skill importable for its tests.

The skill ships as ``skills/public/iran-market-analysis/scripts/iran_market``,
which is not an installed package, so the scripts directory is added to
``sys.path`` here (the same trick ``tests/skills/skill_loader.py`` uses for the
single-file skills).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_SCRIPTS = REPO_ROOT / "skills" / "public" / "iran-market-analysis" / "scripts"

if str(SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SKILL_SCRIPTS))
