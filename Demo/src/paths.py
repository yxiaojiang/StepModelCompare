"""Locations shared by source builds and the installed application."""

import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE if getattr(sys, "frozen", False) else SOURCE.parent
WEB = SOURCE / "web"
