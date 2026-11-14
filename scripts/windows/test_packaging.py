"""Focused checks for accidental profile/credential inclusion and missing runtime files."""

import struct
import tempfile
import unittest
from pathlib import Path

from native_signing import assert_signature_only
from verify_payload import verify


class PayloadChecks(unittest.TestCase):
    def test_unsigned_optional_tcl_toolkit_is_not_shipped(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            library = root / "python/DLLs/tcl86t.dll"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"upstream optional desktop toolkit")
            with self.assertRaisesRegex(ValueError, "Unused optional Tcl/Tk"):
                verify(root)

    def test_signed_pe_allows_only_authenticode_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            before = bytearray(512)
            before[:2] = b"MZ"
            struct.pack_into("<I", before, 0x3C, 64)
            before[64:68] = b"PE\0\0"
            struct.pack_into("<H", before, 88, 0x20B)
            after = bytearray(before)
            struct.pack_into("<I", after, 88 + 64, 12345)
            struct.pack_into("<II", after, 88 + 112 + 32, 512, 16)
            after.extend(b"\0" * 16)
            original, signed = root / "original.exe", root / "signed.exe"
            original.write_bytes(before)
            signed.write_bytes(after)
            assert_signature_only(original, signed)
            after[400] = 1
            signed.write_bytes(after)
            with self.assertRaisesRegex(ValueError, "changed executable content"):
                assert_signature_only(original, signed)

    def test_missing_runtime_is_refused(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            self.assertRaisesRegex(ValueError, "python/python.exe"),
        ):
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
