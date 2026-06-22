import os
import unittest
from types import SimpleNamespace
from unittest import mock

import crawler


class PpomppuOpenClawConfigTests(unittest.TestCase):
    def test_default_model_uses_openclaw_codex_gpt(self):
        if os.getenv("OPENCLAW_PPOMPPU_MODEL"):
            self.skipTest("OPENCLAW_PPOMPPU_MODEL is overridden in the environment")

        self.assertEqual(crawler.PPOMPPU_OPENCLAW_MODEL, "openai-codex/gpt-5.5")

    def test_extract_openclaw_output_text_reads_first_text_output(self):
        raw_stdout = '{"outputs":[{"text":"{\\"ok\\":true}","mediaUrl":null}]}'

        self.assertEqual(crawler._extract_openclaw_output_text(raw_stdout), '{"ok":true}')

    def test_run_ppomppu_openclaw_model_uses_gateway_and_image_files(self):
        completed = SimpleNamespace(
            returncode=0,
            stdout='{"outputs":[{"text":"{\\"ok\\":true}"}]}',
            stderr="",
        )

        with mock.patch.object(crawler.subprocess, "run", return_value=completed) as run_mock:
            output = crawler._run_ppomppu_openclaw_model(
                "prompt",
                [{"content": b"image-bytes", "suffix": ".jpg"}],
            )

        self.assertEqual(output, '{"ok":true}')
        command = run_mock.call_args.args[0]
        self.assertIn("--gateway", command)
        self.assertIn("--json", command)
        self.assertEqual(command[command.index("--model") + 1], crawler.PPOMPPU_OPENCLAW_MODEL)
        self.assertEqual(command[command.index("--prompt") + 1], "prompt")
        image_path = command[command.index("--file") + 1]
        self.assertTrue(image_path.endswith(".jpg"))


if __name__ == "__main__":
    unittest.main()
