"""PyInstaller entry. A frozen worker must dispatch before importing Qt."""

import sys

if __name__ == "__main__":
    if "--worker" in sys.argv:
        sys.argv.remove("--worker")
        from ai_note_taker.cli import main
    else:
        from ai_note_taker.desktop import main
    main()
