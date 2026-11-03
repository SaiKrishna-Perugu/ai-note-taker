AI Note Taker - install guide
=============================

Install
  1. Extract this whole zip to a folder (right-click > Extract All).
  2. Double-click  Install.cmd
  3. Open "AI Note Taker" from the Start menu or your desktop.

No administrator rights are needed. The app is installed for your user account only,
in  %LOCALAPPDATA%\Programs\AI Note Taker

Uninstall
  Settings > Apps > Installed apps > AI Note Taker > Uninstall.
  Your recordings (Documents\AI Note Taker) and settings (%APPDATA%\AI Note Taker) are kept.

First run
  * "Save audio only" records without any AI and needs no key.
  * For transcripts and notes, press Record once: the app creates a settings file and opens it.
    Paste your Gemini API key (https://aistudio.google.com/apikey) after GEMINI_API_KEY=,
    save, then press Record again. Audio is sent to Google's Gemini API when AI is on.
  * Record meetings only with the participants' knowledge.

Windows security prompts
  This build is not code-signed, so Windows SmartScreen may show "Windows protected your PC"
  for Install.cmd: choose "More info" > "Run anyway". The app itself runs on the official,
  signed Python runtime from python.org.

Requirements: Windows 10 or 11, 64-bit. Nothing else to install.
