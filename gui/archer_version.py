"""Release version shared by source and packaged installations."""
from pathlib import Path

VERSION = Path(__file__).with_name("VERSION").read_text(encoding="ascii").strip()
