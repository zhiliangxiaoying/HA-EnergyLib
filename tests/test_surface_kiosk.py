"""Exercise the Linux kiosk launcher against delayed/incorrect HTTP responses."""

import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "tools/surface/ha-kiosk.sh"


@unittest.skipUnless(
    os.name == "posix" and all(shutil.which(c) for c in ("bash", "curl", "flock")),
    "requires Linux kiosk tools",
)
class KioskStartupTests(unittest.TestCase):
    def test_waits_for_target_dashboard_and_prevents_duplicate_launch(self):
        state = {"response": "missing"}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != "/energylib-home/twin":
                    self.send_response(200)
                    body = b"<home-assistant></home-assistant>"
                elif state["response"] == "missing":
                    self.send_response(404)
                    body = b"Not Found"
                elif state["response"] == "wrong":
                    self.send_response(200)
                    body = b"<html>Service starting</html>"
                else:
                    self.send_response(200)
                    body = b"<!doctype html><home-assistant></home-assistant>"
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory(prefix="energylib-kiosk-test-") as tmp:
                root = Path(tmp)
                target = f"http://127.0.0.1:{server.server_port}/energylib-home/twin"
                result = root / "browser-args.json"
                browser = root / "firefox"
                # Substitute only Firefox: readiness uses real curl/HTTP and flock.
                browser.write_text(
                    "#!/usr/bin/env python3\nimport json,os,sys\n"
                    "from pathlib import Path\n"
                    "Path(os.environ['KIOSK_TEST_RESULT']).write_text(json.dumps(sys.argv[1:]))\n"
                )
                browser.chmod(0o700)
                env = os.environ | {
                    "PATH": str(root) + os.pathsep + os.environ["PATH"],
                    "XDG_RUNTIME_DIR": str(root),
                    "ENERGYLIB_KIOSK_URL": target,
                    "KIOSK_TEST_RESULT": str(result),
                }
                process = subprocess.Popen(
                    ["bash", str(LAUNCHER)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                try:
                    time.sleep(2.5)
                    self.assertIsNone(process.poll())
                    self.assertFalse(result.exists(), "must not open a 404 page")
                    duplicate = subprocess.run(
                        ["bash", str(LAUNCHER)], env=env, capture_output=True, timeout=3
                    )
                    self.assertEqual(duplicate.returncode, 0)
                    self.assertFalse(result.exists())
                    state["response"] = "wrong"
                    time.sleep(2.5)
                    self.assertIsNone(process.poll())
                    self.assertFalse(result.exists(), "200 alone must not start Firefox")
                    state["response"] = "ready"
                    out, err = process.communicate(timeout=8)
                    self.assertEqual(process.returncode, 0, err.decode())
                    self.assertIn(b"dashboard ready", out)
                    self.assertEqual(json.loads(result.read_text()), ["--kiosk", target])
                finally:
                    if process.poll() is None:
                        process.terminate()
                        process.communicate(timeout=8)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
