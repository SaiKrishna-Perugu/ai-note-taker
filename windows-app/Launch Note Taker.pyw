"""Launch the desktop copy with its existing project environment, without a console."""

import site
import sys
from pathlib import Path

folder = Path(__file__).resolve().parent
sys.path.insert(0, str(folder / "src"))
site.addsitedir(str(folder / ".venv" / "Lib" / "site-packages"))

from ai_note_taker.desktop import main

if __name__ == "__main__":
    main()
