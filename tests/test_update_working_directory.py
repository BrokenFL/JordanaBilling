"""Run the launcher's real bootstrap before removing its inherited directory."""
import json
import os
import select
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]


class UpdateWorkingDirectoryTests(unittest.TestCase):
    def test_pdf_generation_survives_removal_of_inherited_app_directory(self):
        launchers = [
            PROJECT / "scripts/launch_installed_app.sh",
            PROJECT / "Jordana Billing.app/Contents/Resources/launch_installed_app.sh",
        ]
        for launcher in launchers:
            with self.subTest(launcher=launcher.relative_to(PROJECT)):
                self._check_launcher(launcher)

    def _check_launcher(self, launcher):
        with tempfile.TemporaryDirectory(prefix="jordana-launch-cwd-") as td:
            root = Path(td)
            inherited = root / "previous-app-resources"
            inherited.mkdir()
            support = root / "application-support"
            bootstrap = root / "launch-bootstrap.sh"
            # Execute the production initialization unchanged. Stop before
            # config/DB validation so no installed app or live DB is involved.
            prologue = launcher.read_text().split("sanitize_message()", 1)[0]
            bootstrap.write_text(prologue + '\nexec "$TEST_PYTHON" "$TEST_PROBE"\n')
            probe = root / "pdf-probe.py"
            probe.write_text('''
import json, os, sys
from jordana_invoice.invoice_pdf import generate_draft_pdf_bytes
print("READY", flush=True)
sys.stdin.readline()
pdf = generate_draft_pdf_bytes(
    {"status": "draft", "total_cents": 15000, "currency": "USD",
     "business_name_snapshot": "Example Practice", "bill_to_name_snapshot": "Alex Example"},
    [{"service_date": "2026-08-10", "participants_snapshot": "Alex Example",
      "description_snapshot": "Example service", "duration_minutes": 60,
      "line_amount_cents": 15000}],
)
print(json.dumps({"cwd": os.getcwd(), "valid_pdf": pdf.startswith(b"%PDF"), "bytes": len(pdf)}), flush=True)
''')
            env = {**os.environ, "JORDANA_APP_SUPPORT_DIR": str(support),
                   "TEST_PYTHON": sys.executable, "TEST_PROBE": str(probe)}
            # Preserve the caller's chosen source/package import path.
            env["PYTHONPATH"] = os.pathsep.join(
                str(Path(p).resolve()) for p in env.get("PYTHONPATH", "").split(os.pathsep) if p
            )
            process = subprocess.Popen(["bash", str(bootstrap)], cwd=inherited,
                env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True)
            try:
                readable, _, _ = select.select([process.stdout], [], [], 15)
                self.assertTrue(readable, "PDF probe did not start")
                self.assertEqual(process.stdout.readline().strip(), "READY")
                inherited.rmdir()  # Same lifetime change as app replacement/eject.
                output, error = process.communicate("render\n", timeout=20)
                self.assertEqual(process.returncode, 0, error)
                result = json.loads(output)
                self.assertEqual(Path(result["cwd"]).resolve(), support.resolve())
                self.assertTrue(result["valid_pdf"])
                self.assertGreater(result["bytes"], 1000)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()
