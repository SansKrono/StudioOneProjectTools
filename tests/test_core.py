"""
Core regression tests. Run from the repo root:

    python -m unittest discover -s tests -v
"""

from pathlib import Path
import os
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import everything
from core.audio_manager import build_audio_table_rows, extract_audio_references_from_text
from core.consolidate import consolidate_project_to_new_folder, verify_consolidated_song
from core.crash_analyzer import analyze_crash_file, parse_crash_text, match_plugins_to_crash
from core.minidump_reader import analyze_minidump, format_minidump_report, _project_fx_match_score
from core.missing_files import recover_missing_to_media_and_relink, search_missing_candidates_everything
from core.plugin_manager import parse_plugins, modify_selected_fx, remove_bypassed_fx
from core.relink import Relinker
from core.report_generator import export_csv_report
from core.song_archive import read_text_from_archive, read_text_members, write_modified_song_copy
from models.missing_file import MissingFileCandidate
from tests.fixtures import FakePlugin, make_minidump, make_project


def mediapool(song: Path) -> str:
    return read_text_from_archive(song, "Song/mediapool.xml")


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.fx = make_project(self.root)
        self.song, self.proj = self.fx["song"], self.fx["project"]
        self.rows = build_audio_table_rows(self.proj, read_text_members(self.song))

    def tearDown(self):
        self._tmp.cleanup()

    def missing_candidate(self) -> MissingFileCandidate:
        ref = next(r.reference for r in self.rows if r.status == "MISSING")
        found = self.fx["found"]
        return MissingFileCandidate(ref, found.name, found, found.stat().st_size, None, "FILENAME ONLY")


class PluginTests(ProjectTestCase):
    def test_parse_plugins_by_channel(self):
        got = [(p.track_name, p.slot_name, p.plugin_name, p.bypassed) for p in parse_plugins(self.song)]
        self.assertEqual(got, [
            ("Guitar DI", "FX0", "Pro-Q 3", True),
            ("Guitar DI", "FX2", "Archetype Gojira", False),
            ("Drums Bus", "FX0", "Pro-C 2", False),
            ("Main", "FX0", "Limiter", True),
        ])

    def test_empty_slot_does_not_truncate_parsing(self):
        # The original matcher never terminated on <Attributes name="FX1"/> and dropped every later FX.
        self.assertEqual(len(parse_plugins(self.song)), 4)

    def test_bypass_and_remove_write_copies(self):
        out = self.root / "mod.song"
        _, changed = modify_selected_fx(self.song, out, {2}, "bypass")
        self.assertEqual(changed, ["Pro-C 2"])
        self.assertTrue(parse_plugins(out)[2].bypassed)

        out2 = self.root / "clean.song"
        remove_bypassed_fx(self.song, out2)
        self.assertEqual([p.plugin_name for p in parse_plugins(out2)], ["Archetype Gojira", "Pro-C 2"])
        # The original is untouched.
        self.assertEqual(len(parse_plugins(self.song)), 4)

    def test_refuses_to_overwrite_original(self):
        with self.assertRaises(ValueError):
            write_modified_song_copy(self.song, self.song, lambda n, t: t)

    def test_binary_members_are_preserved(self):
        out = self.root / "copy.song"
        modify_selected_fx(self.song, out, {0}, "unbypass")
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.read("binary.bin"), b"\x00\x01\x02")


class AudioAnalysisTests(ProjectTestCase):
    def test_statuses(self):
        by_status = {}
        for r in self.rows:
            by_status.setdefault(r.status, set()).add(r.filename)
        self.assertEqual(by_status["MISSING"], {"lost guitar.wav"})
        self.assertEqual(by_status["EXTERNAL"], {"vox.wav"})
        self.assertEqual(by_status["UNUSED"], {"kick_copy.wav", "unused take.wav"})
        self.assertIn("mix.wav", by_status["NORMAL"])
        self.assertIn("pad.aiff", by_status["NORMAL"])

    def test_aiff_and_wave_extensions_are_not_truncated(self):
        refs = extract_audio_references_from_text('<a f="Media\\pad.aiff"/><b f="x.wave"/>')
        self.assertEqual(refs, ["Media\\pad.aiff", "x.wave"])


class RelinkTests(ProjectTestCase):
    def test_restore_missing_relinks_to_media(self):
        out = self.root / "relinked.song"
        recover_missing_to_media_and_relink(self.song, out, self.proj, [self.missing_candidate()])
        text = mediapool(out)
        self.assertIn('file="Media\\lost guitar.wav"', text)
        self.assertNotIn("Media\\Media", text)
        self.assertNotIn("Old Takes", text)
        # Everything else is untouched.
        self.assertIn('file="Media\\kick.wav"', text)

    def test_relinker_is_single_pass(self):
        r = Relinker()
        r.add_filename("a.wav", "Media\\a.wav")
        new, hits = r.apply('<x f="Old\\a.wav"/><y f="a.wav"/><z f="C:\\Keep\\a.wav"/>')
        self.assertEqual(new, '<x f="Media\\a.wav"/><y f="Media\\a.wav"/><z f="C:\\Keep\\a.wav"/>')
        self.assertEqual(len(hits), 2)


class ConsolidateTests(ProjectTestCase):
    def test_consolidate(self):
        out_parent = self.root / "Out"
        out_parent.mkdir()
        res = consolidate_project_to_new_folder(
            song_path=self.song, project_dir=self.proj, output_parent=out_parent,
            audio_rows=self.rows, missing_candidates=[self.missing_candidate()], copy_unused=True,
        )
        files = {str(p.relative_to(res.output_dir)) for p in res.output_dir.rglob("*") if p.is_file()}
        for expected in ("Media\\kick.wav", "Bounces\\mix.wav", "External\\vox.wav", "Media\\lost guitar.wav",
                         "_Unused\\unused take.wav", "_Duplicates\\snare.wav"):
            self.assertIn(expected.replace("\\", os.sep), files)

        text = mediapool(res.output_song)
        self.assertIn('file="External\\vox.wav"', text)
        self.assertIn('file="Media\\lost guitar.wav"', text)
        self.assertEqual(res.recovered_missing, 1)
        self.assertEqual(res.verify_missing_after, 0, res.verify_missing_refs_after)
        self.assertEqual(res.errors, [])
        # The original project is untouched.
        self.assertIn("Old Takes\\lost guitar.wav", mediapool(self.song))

    def test_verify_detects_reference_that_only_resolves_by_name(self):
        out_dir = self.root / "Broken"
        (out_dir / "Media").mkdir(parents=True)
        (out_dir / "Media" / "lost guitar.wav").write_bytes(b"x")
        broken_song = out_dir / "b.song"
        with zipfile.ZipFile(broken_song, "w") as z:
            z.writestr("Song/mediapool.xml", '<a file="Old Takes\\lost guitar.wav"/>')
        _total, broken = verify_consolidated_song(broken_song, out_dir)
        self.assertEqual(broken, ["Old Takes\\lost guitar.wav"])


class CrashAnalyzerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.dump = Path(cls._tmp.name) / "Studio One.exe.1.dmp"
        cls.exc_tid = make_minidump(cls.dump)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_exception_thread_evidence_is_kept(self):
        mini = analyze_minidump(self.dump)
        self.assertEqual(mini.error, "")
        self.assertEqual(mini.exception.code, 0xC0000005)
        self.assertTrue(mini.faulting_module.name.endswith("KERNELBASE.dll"))
        self.assertTrue(mini.exception_thread_stack_hits)
        self.assertTrue(all("Gojira" in h.module.name for h in mini.exception_thread_stack_hits
                            if "Windows" not in h.module.name))

    def test_report_blames_exception_thread_plugin(self):
        report = format_minidump_report(analyze_minidump(self.dump), ["Pro-Q 3", "Archetype Gojira", "Pro-C 2"])
        strong = report.split("(strong evidence)", 1)[1].split("(supporting evidence)", 1)[0]
        self.assertIn("Archetype Gojira  ->", strong)
        self.assertNotIn("Pro-Q 3  ->", strong)
        self.assertNotIn("Pro-C 2", report.split("Loaded project FX", 1)[0])

    def test_fx_name_matching(self):
        pro_q = r"C:\Program Files\Common Files\VST3\FabFilter Pro-Q 3.vst3\Contents\x86_64-win\FabFilter Pro-Q 3.vst3"
        self.assertGreater(_project_fx_match_score("Pro-Q 3", pro_q)[0], 0)
        self.assertGreater(_project_fx_match_score("FabFilter Pro-Q 3", pro_q)[0], 0)
        self.assertEqual(_project_fx_match_score("Pro-C 2", pro_q)[0], 0)
        self.assertEqual(_project_fx_match_score("Pro-Q 4", pro_q)[0], 0)

    def test_dump_analysis_is_fast(self):
        big = Path(self._tmp.name) / "big.dmp"
        make_minidump(big, n_threads=40, stack_kb=256, filler_modules=300)
        start = time.perf_counter()
        analyze_crash_file(big, plugins=[FakePlugin("Pro-Q 3")], output_dir=Path(self._tmp.name) / "r")
        # The original implementation took ~35 s on this input.
        self.assertLess(time.perf_counter() - start, 5)

    def test_report_written_and_display_text(self):
        out = Path(self._tmp.name) / "reports"
        r = analyze_crash_file(self.dump, plugins=[FakePlugin("Archetype Gojira")], output_dir=out)
        self.assertTrue(r.report_path.is_file())
        self.assertEqual(list(out.iterdir()), [r.report_path])  # one report, not two copies

    def test_text_log_hints_are_english(self):
        text = "Faulting application name: Studio One.exe, Faulting module name: Archetype Gojira.vst3\nException code: 0xc0000005\n"
        r = parse_crash_text(Path("x.log"), text)
        self.assertEqual(r.faulting_module, "Archetype Gojira.vst3")
        self.assertTrue(all(h.isascii() for h in r.hints))
        self.assertEqual(len(match_plugins_to_crash(r, [FakePlugin("Archetype Gojira"), FakePlugin("Pro-C 2")])), 1)


class EverythingTests(unittest.TestCase):
    def test_es_exe_is_never_taken_from_cwd(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "es.exe").write_bytes(b"MZ")
            old = os.getcwd()
            os.chdir(d)
            try:
                with mock.patch.dict(os.environ, {"PATH": ".", "ProgramFiles": d + "\\nope", "ProgramFiles(x86)": d + "\\nope"}), \
                        mock.patch.object(everything, "app_dir", return_value=Path(d) / "app"):
                    self.assertIsNone(everything.find_es_exe(None))
                    self.assertIsNone(everything.find_es_exe("es.exe"))  # relative configured path is ignored
            finally:
                os.chdir(old)

    def test_search_query_is_quoted_and_filtered(self):
        with tempfile.TemporaryDirectory() as d:
            exact = Path(d) / "-n 0 kick.wav"
            exact.write_bytes(b"x")
            near = Path(d) / "big-n 0 kick.wav"
            near.write_bytes(b"x")
            completed = subprocess.CompletedProcess([], 0, stdout=f"{near}\n{exact}\n", stderr="")
            with mock.patch.object(everything, "find_es_exe", return_value=Path("C:/es.exe")), \
                    mock.patch.object(everything.subprocess, "run", return_value=completed) as run:
                got = everything.search_everything_by_filename("-n 0 kick.wav")
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[-1], '"-n 0 kick.wav"')  # never a bare option-like argument
            self.assertEqual(run.call_args.kwargs["creationflags"], getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(got, [exact])

    def test_missing_search_without_es_exe_does_not_crash(self):
        # The original raised NameError: name 'mode' is not defined.
        with tempfile.TemporaryDirectory() as d:
            fx = make_project(Path(d))
            rows = build_audio_table_rows(fx["project"], read_text_members(fx["song"]))
            with mock.patch.object(everything, "find_es_exe", return_value=None):
                self.assertEqual(search_missing_candidates_everything(rows), [])


class ReportTests(unittest.TestCase):
    def test_csv_formula_injection_is_neutralised(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "r.csv"
            export_csv_report(out, None, [FakePlugin('=HYPERLINK("http://x")')], [])
            self.assertIn("'=HYPERLINK", out.read_text(encoding="utf-8-sig"))


if __name__ == "__main__":
    unittest.main()
