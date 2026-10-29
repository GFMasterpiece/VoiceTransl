
import ast
import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LegacyDesktopASRRemovalTests(unittest.TestCase):
    def test_desktop_entrypoints_do_not_reference_crispasr(self):
        for relative_path in (
            "app.py",
            "i18n.py",
            "app.spec",
            "app-macos.spec",
            "README_EN.md",
            ".github/workflows/build.yaml",
        ):
            text = (ROOT / relative_path).read_text(encoding="utf-8").lower()
            self.assertNotIn("crispasr", text, relative_path)
            self.assertNotIn("crisp asr", text, relative_path)

    def test_desktop_text_has_all_used_translation_keys(self):
        import i18n

        tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
        used = {
            node.args[0].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        for language, table in i18n.TRANSLATIONS.items():
            self.assertEqual(sorted(used - table.keys()), [], language)

    def test_audio_input_without_sidecar_srt_stops_before_process_start(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        main_worker = next(
            node for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef) and node.name == "MainWorker"
        )
        run_method = next(
            node for node in main_worker.body
            if isinstance(node, ast.FunctionDef) and node.name == "run"
        )
        run_source = ast.get_source_segment(source, run_method)
        self.assertIsNotNone(run_source)
        unavailable = run_source.index("status_local_asr_unavailable")
        self.assertIn("if not os.path.exists(existing_srt):", run_source)
        self.assertIn("self._emit_status", run_source)
        self.assertNotIn("_process_single_audio", run_source)
        self.assertNotIn("enable_transcription", run_source)
        branch_start = run_source.index("if not os.path.exists(existing_srt):")
        branch_end = run_source.index("self._emit_status(_(\"status_existing_srt_found\"", branch_start)
        branch = run_source[branch_start:branch_end]
        self.assertIn("status_local_asr_unavailable", branch)
        self.assertIn("continue", branch)
        self.assertNotIn("self._start_process(", branch)

if __name__ == "__main__":
    unittest.main()
