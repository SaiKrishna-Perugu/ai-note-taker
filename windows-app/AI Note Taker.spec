# Build with: uv run pyinstaller "AI Note Taker.spec"
from PyInstaller.utils.hooks import collect_submodules

analysis = Analysis(
    ["desktop_entry.py"],
    pathex=["src"],
    binaries=[],
    datas=[],
    hiddenimports=collect_submodules("google.genai", filter=lambda name: ".tests" not in name and "._test" not in name)
                  + ["pyaudiowpatch", "ai_note_taker.cli"],
    hookspath=[],
    runtime_hooks=[],
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtPdf",
              "tkinter", "sarvamai", "pycaw", "comtypes", "pytest"],
    noarchive=False,
)
archive = PYZ(analysis.pure)
exe = EXE(archive, analysis.scripts, [], exclude_binaries=True, name="AI Note Taker",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, icon="packaging/app.ico")
distribution = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False,
                       name="AI Note Taker")
