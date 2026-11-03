import os
import re
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from ai_note_taker import theme


def luminance(colour: str) -> float:
    channels = [int(colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((luminance(foreground), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# (description, foreground token, background token, minimum WCAG contrast ratio)
READABILITY = [
    ("text on canvas", "text", "bg", 7), ("text on card", "text", "card", 7),
    ("text on panel", "text", "panel", 7), ("text on sidebar", "text", "sidebar", 7),
    ("muted on canvas", "muted", "bg", 4.5), ("muted on card", "muted", "card", 4.5),
    ("muted on panel", "muted", "panel", 4.5), ("muted on sidebar", "muted", "sidebar", 4.5),
    ("status chip", "status_fg", "status_bg", 4.5), ("ok chip", "chip_ok_fg", "chip_ok_bg", 4.5),
    ("warn chip", "chip_warn_fg", "chip_warn_bg", 4.5), ("off chip", "chip_off_fg", "chip_off_bg", 4.5),
    ("notice", "notice_text", "notice_bg", 4.5), ("button", "text", "btn_bg", 7),
    ("primary button", "primary_fg", "primary", 4.5), ("primary hover", "primary_fg", "primary_hover", 4.5),
    ("input", "text", "input_bg", 7), ("inactive tab", "tab_fg", "card", 4.5),
    ("selected tab", "tab_sel_fg", "card", 4.5), ("selected list row", "text", "list_sel", 7),
    ("selected menu row", "text", "menu_sel", 7), ("tooltip", "tip_fg", "tip_bg", 7),
    ("waveform on card", "wave_active", "card", 3),
]


class ThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_both_palettes_define_the_same_tokens(self):
        self.assertEqual(set(theme.LIGHT), set(theme.DARK))
        self.assertEqual(set(theme.CAPSULE_LIGHT), set(theme.CAPSULE_DARK))

    def test_stylesheets_are_fully_resolved_and_differ(self):
        light, dark = theme.style_sheet("light"), theme.style_sheet("dark")
        for sheet in (light, dark):
            self.assertNotRegex(sheet, r"\$[a-z_]+")
            self.assertIn("QPushButton#primary", sheet)
        self.assertIn(theme.DARK["bg"], dark)
        self.assertNotIn(theme.DARK["bg"], light)
        self.assertNotEqual(light, dark)

    def test_every_colour_is_a_valid_hex_value(self):
        for palette in (theme.LIGHT, theme.DARK):
            for name, value in palette.items():
                self.assertRegex(value, r"^#[0-9a-fA-F]{6}$", name)

    def test_text_stays_readable_in_both_themes(self):
        for name, palette in (("light", theme.LIGHT), ("dark", theme.DARK)):
            for description, foreground, background, minimum in READABILITY:
                with self.subTest(theme=name, pair=description):
                    self.assertGreaterEqual(contrast(palette[foreground], palette[background]), minimum)

    def test_dark_is_actually_dark_and_light_is_light(self):
        self.assertLess(luminance(theme.DARK["bg"]), 0.05)
        self.assertGreater(luminance(theme.LIGHT["bg"]), 0.9)
        self.assertGreater(luminance(theme.DARK["text"]), 0.5)
        self.assertLess(luminance(theme.LIGHT["text"]), 0.1)

    def test_resolve_honours_an_explicit_choice_and_follows_windows_otherwise(self):
        self.assertEqual(theme.resolve("light"), "light")
        self.assertEqual(theme.resolve("dark"), "dark")
        with patch("ai_note_taker.theme.system_is_dark", return_value=True):
            self.assertEqual(theme.resolve("system"), "dark")
            self.assertEqual(theme.resolve("light"), "light")      # explicit beats the system
        with patch("ai_note_taker.theme.system_is_dark", return_value=False):
            self.assertEqual(theme.resolve("system"), "light")
            self.assertEqual(theme.resolve("dark"), "dark")

    def test_unknown_preferences_fall_back_to_system(self):
        for bad in (None, "", "purple", 3):
            self.assertEqual(theme.normalize(bad), "system")
        self.assertEqual(theme.normalize("dark"), "dark")

    def test_capsule_colours_differ_per_theme_and_shadow_has_alpha(self):
        light, dark = theme.capsule_colors("light"), theme.capsule_colors("dark")
        self.assertNotEqual(light["body"].name(), dark["body"].name())
        self.assertNotEqual(light["ink"].name(), dark["ink"].name())
        for colors in (light, dark):
            self.assertLess(colors["shadow"].alpha(), 255)
            self.assertGreater(colors["shadow"].alpha(), 0)

    def test_capsule_icons_are_readable_on_the_capsule_body(self):
        for name in ("light", "dark"):
            colors = theme.capsule_colors(name)
            self.assertGreaterEqual(contrast(colors["ink"].name(), colors["body"].name()), 7, name)
            self.assertGreaterEqual(contrast(colors["record"].name(), colors["body"].name()), 3, name)

    def test_color_scheme_override_is_applied_and_cleared(self):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() == "offscreen":
            self.skipTest("the offscreen test platform ignores colour-scheme overrides (verified on Windows)")

        self.addCleanup(theme.apply_color_scheme, "system")
        theme.apply_color_scheme("dark")
        self.assertEqual(QGuiApplication.styleHints().colorScheme(), Qt.ColorScheme.Dark)
        theme.apply_color_scheme("light")
        self.assertEqual(QGuiApplication.styleHints().colorScheme(), Qt.ColorScheme.Light)


if __name__ == "__main__":
    unittest.main()
