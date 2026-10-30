import io
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app
import desktop_runtime


class DesktopRuntimeTests(unittest.TestCase):
    def test_split_command_preserves_quoted_argument(self):
        self.assertEqual(
            desktop_runtime.split_command('tool --model "a model.gguf"'),
            ["tool", "--model", "a model.gguf"],
        )

    def test_format_command_quotes_arguments_for_host_shell(self):
        self.assertIn("a b", desktop_runtime.format_command(["tool", "a b"]))

    def test_resolve_executable_accepts_relative_and_path_commands(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            tool = Path(temp_dir) / "tool"
            tool.write_text("", encoding="utf-8")
            tool.chmod(0o755)
            with patch.object(Path, "cwd", return_value=Path(temp_dir)):
                self.assertEqual(
                    desktop_runtime.resolve_executable("tool"), tool.resolve()
                )
        self.assertIsNotNone(desktop_runtime.resolve_executable("python3"))

    def test_find_tool_prefers_bundled_path_then_path(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled = Path(temp_dir) / "ffmpeg"
            bundled.write_text("", encoding="utf-8")
            self.assertEqual(
                desktop_runtime.find_tool("missing", bundled_path=bundled),
                str(bundled.resolve()),
            )
        with self.assertRaises(FileNotFoundError):
            desktop_runtime.find_tool("definitely-not-a-real-tool-xyz")

    def test_ffmpeg_paths_accepts_bundled_pair(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "ffmpeg").mkdir()
            for name in ("ffmpeg", "ffprobe"):
                (root / "ffmpeg" / name).write_text("", encoding="utf-8")
            with patch.object(Path, "cwd", return_value=root):
                self.assertEqual(
                    desktop_runtime.ffmpeg_paths(),
                    (
                        str((root / "ffmpeg" / "ffmpeg").resolve()),
                        str((root / "ffmpeg" / "ffprobe").resolve()),
                    ),
                )

    def test_popen_and_run_apply_flags_once(self):
        class Process:
            pass

        with patch.object(desktop_runtime.subprocess, "Popen", return_value=Process()) as start:
            desktop_runtime.popen(["tool"], stdout=None, creationflags=7)
        self.assertEqual(start.call_args.kwargs["creationflags"], 7)
        with patch.object(desktop_runtime.subprocess, "run", return_value=Process()) as run:
            desktop_runtime.run(["tool"], creationflags=9, check=False)
        self.assertEqual(run.call_args.kwargs["creationflags"], 9)

    def test_terminate_process_terminates_and_reaps(self):
        class Process:
            def __init__(self):
                self.running = True
                self.terminated = False
                self.killed = False

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                self.terminated = True
                self.running = False

            def wait(self, timeout=None):
                return 0

            def kill(self):
                self.killed = True
                self.running = False

        process = Process()
        desktop_runtime.terminate_process(process)
        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)

    def test_terminate_process_escalates_after_timeout(self):
        class Process:
            def __init__(self):
                self.terminate_calls = 0
                self.kill_calls = 0

            def poll(self):
                return None

            def terminate(self):
                self.terminate_calls += 1
                raise OSError("terminate failed")

            def kill(self):
                self.kill_calls += 1

            def wait(self, timeout=None):
                return 0

        process = Process()
        desktop_runtime.terminate_process(process)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(process.kill_calls, 1)

    def test_translation_process_is_registered_and_removed(self):
        class Process:
            def __init__(self):
                self.stdout = io.StringIO("")

            def poll(self):
                return 0

            def wait(self, timeout=None):
                return 0

        process = Process()
        active = []
        events = []

        class Queue:
            def put(self, target, text):
                events.append((target, text))

        tf_dict = {
            "base_path": "/tmp/audio",
            "json_src": "/tmp/audio.json",
            "output_dir": "/tmp/out",
            "output_format": "目标SRT",
            "orig_srt_path": "",
        }

        with patch.object(desktop_runtime, "popen", return_value=process) as start, \
             patch.object(app.shutil, "copy"), \
             patch.object(app.ConcurrentTranslationPool, "_create_workspace_impl", return_value="/tmp/work"), \
             patch.object(app.ConcurrentTranslationPool, "_prepare_config_impl"), \
             patch.object(app.ConcurrentTranslationPool, "_generate_output_impl"):
            app.ConcurrentTranslationPool._translate_one_impl(
                tf_dict, 0, "/tmp/project", "/tmp/config.yaml", "engine", Queue(),
                active_procs=active, procs_lock=threading.Lock(),
            )

        start.assert_called_once()
        self.assertEqual(active, [])
    def test_pool_terminates_all_before_waiting(self):
        events = []

        class Process:
            def __init__(self, name):
                self.name = name
                self.running = True

            def poll(self):
                return None if self.running else 0

            def terminate(self):
                events.append(("terminate", self.name))

            def wait(self, timeout=None):
                events.append(("wait", self.name))
                self.running = False

        class Queue:
            def put(self, target, text):
                events.append(("message", text))

            def drain_all(self, timeout=None):
                return []

        first = Process("first")
        second = Process("second")
        with patch.object(app, "_stream_proc_to_queue", lambda *args: None):
            pool = app.ConcurrentTranslationPool(
                "/tmp/project", "/tmp/config.yaml", 2,
                threading.Event(), Queue(),
            )
        pool._active_translate_procs.extend([first, second])
        pool._terminate_active_translate_procs()

        self.assertEqual(events[:2], [("terminate", "first"), ("terminate", "second")])
        self.assertEqual(events[2:], [("wait", "first"), ("wait", "second")])
        self.assertEqual(pool._active_translate_procs, [])

    def test_shutdown_command_matches_current_platform(self):
        command = desktop_runtime.shutdown_command()
        self.assertTrue(command)
        if os.name == "nt":
            self.assertEqual(command[0], "shutdown")
        else:
            self.assertIn("-h", command)


if __name__ == "__main__":
    unittest.main()