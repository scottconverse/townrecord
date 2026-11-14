"""Focused checks for accidental profile/credential inclusion and missing runtime files."""

import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from native_signing import assert_signature_only
from seed_payload import (
    ORIGINAL,
    RELATIVE,
    derive,
    digest,
    file_map,
    filesystem_path,
    verify_derived,
    verify_seed,
    write,
)
from verify_payload import verify


class PayloadChecks(unittest.TestCase):
    def test_seed_hashes_windows_long_paths_without_policy_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = filesystem_path(Path(folder))
            relative = "/".join(["package" * 3] * 12) + "/license.txt"
            file = root / relative
            file.parent.mkdir(parents=True)
            file.write_bytes(b"upstream license fixture")
            self.assertEqual(file_map(root), {relative: digest(file)})
            shutil.rmtree(root)

    def test_seed_derives_only_signed_file_hashes(self):
        with tempfile.TemporaryDirectory() as folder:
            payload = Path(folder)
            root = payload / RELATIVE
            native = root / "Lib/site-packages/native.pyd"
            native.parent.mkdir(parents=True)
            native.write_bytes(b"unsigned native fixture")
            nested = native.parent / "package/manifest.json"
            nested.parent.mkdir()
            nested.write_text('{"package":true}')
            launcher = root / "Scripts/python.exe"
            launcher.parent.mkdir()
            launcher.write_bytes(b"launcher fixture")
            manifest = {
                "format": "townrecord-runtime-seed-v1",
                "tool": "textflowkit",
                "version": "0.1.19",
                "python_version": "3.13",
                "packages": {"textflowkit": "0.1.19"},
                "files": file_map(root),
            }
            (payload / ORIGINAL).parent.mkdir()
            write(payload / ORIGINAL, manifest)
            write(root / "manifest.json", manifest)
            verify_seed(payload)
            original_hash = digest(native)
            native.write_bytes(b"signed native fixture")
            overlay = {
                "files": [
                    {
                        "kind": "payload",
                        "target": native.relative_to(payload).as_posix(),
                        "unsigned_sha256": original_hash,
                        "signed_sha256": digest(native),
                    }
                ]
            }
            receipt = derive(payload, overlay)
            self.assertEqual(
                verify_derived(payload, overlay), receipt["derived_sha256"]
            )
            self.assertIn("Lib/site-packages/package/manifest.json", manifest["files"])
            write(root / "manifest.json", {**manifest, "version": "other"})
            with self.assertRaisesRegex(ValueError, "changed beyond"):
                verify_derived(payload, overlay)

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
