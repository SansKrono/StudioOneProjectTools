"""Drives the real Tk app with dialogs stubbed out. Skipped where no display is available."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.fixtures import make_project

try:
    import tkinter as tk
    tk.Tk().destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "Tk display not available")
class GuiSmokeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fx = make_project(self.root)

        from gui import app as app_module
        self.app_module = app_module
        self.patches = [
            mock.patch.object(app_module.messagebox, name, return_value=True)
            for name in ("showinfo", "showwarning", "showerror", "askyesno")
        ]
        self.patches.append(mock.patch.object(app_module, "open_folder"))
        self.patches.append(mock.patch.object(app_module, "default_report_dir", return_value=self.root / "reports"))
        for p in self.patches:
            p.start()

        self.app = app_module.StudioOneProjectToolsApp()
        self.app.withdraw()
        self.app.song_path = self.fx["song"]
        self.app.project_dir = self.fx["project"]
        self.app.analyze_project()

    def tearDown(self):
        self.app.destroy()
        for p in self.patches:
            p.stop()
        self._tmp.cleanup()

    def test_project_loads_into_tables(self):
        self.assertEqual(len(self.app.plugins), 4)
        self.assertGreater(len(self.app.audio_tree.get_children()), 0)
        self.assertIn("Health Score", self.app.dashboard_text.get("1.0", "end"))

    def test_crash_tab_scans_lazily(self):
        self.assertFalse(self.app.crash_files_loaded)
        self.app.notebook.select(self.app.crash_frame)
        self.app.update()
        self.assertTrue(self.app.crash_files_loaded)

    def test_move_duplicates_never_moves_referenced_files(self):
        target = self.root / "moved"
        with mock.patch.object(self.app_module.filedialog, "askdirectory", return_value=str(target)):
            self.app.move_duplicate_audio()
        moved = sorted(p.name for p in target.iterdir())
        self.assertEqual(moved, ["snare.wav"])  # the unreferenced Media\Sub\snare.wav side file
        self.assertTrue((self.fx["project"] / "Media" / "snare.wav").exists())
        self.assertTrue((self.fx["project"] / "Media" / "kick.wav").exists())

    def test_filters_and_search(self):
        self.app.set_audio_filter("MISSING")
        self.assertEqual(len(self.app.audio_tree.get_children()), 1)
        self.app.clear_audio_search_filter()
        self.app.plugin_search_var.set("gojira")
        rows = [c for parent in self.app.plugins_tree.get_children() for c in self.app.plugins_tree.get_children(parent)]
        self.assertEqual(len(rows), 1)


if __name__ == "__main__":
    unittest.main()
