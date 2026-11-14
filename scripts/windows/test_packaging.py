"""Focused checks for accidental profile/credential inclusion and missing runtime files."""

import tempfile
import unittest
from pathlib import Path

from verify_payload import verify


class PayloadChecks(unittest.TestCase):
    def test_missing_runtime_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, "python/python.exe"):
                verify(Path(folder))

    def test_installer_has_isolated_launchers_and_no_profile_deletion(self):
        script = (Path(__file__).parents[2] / "installer/townrecord.iss").read_text()
        for line in script.splitlines():
            if "townrecord.windows" in line:
                self.assertIn("-I -m townrecord.windows", line)
        self.assertNotIn("[UninstallDelete]", script)
        self.assertIn("PrivilegesRequired=lowest", script)
        self.assertIn("Flags: unchecked", script)


if __name__ == "__main__":
    unittest.main()
