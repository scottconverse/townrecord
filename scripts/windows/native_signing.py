"""Prepare native signing intake and verify signature-only changes on return."""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import shutil
import struct
import zipfile
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "build/windows"
PAYLOAD = WORK / "payload"
NATIVE = {".exe", ".dll", ".pyd"}
SEED = runpy.run_path(
    str(Path(__file__).with_name("seed_payload.py")), run_name="seed_manifest"
)
PAYLOAD = SEED["filesystem_path"](PAYLOAD)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(path: Path, value):
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def child(root: Path, name: str) -> Path:
    parts = PurePosixPath(name).parts
    if (
        not parts
        or "\\" in name
        or ":" in name
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise ValueError(f"Unsafe signing path: {name}")
    target = root.joinpath(*parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Signing path escapes its root")
    return target


def pe_content(path: Path) -> bytes:
    """Ignore only the PE checksum, certificate directory and appended certificate."""
    data = bytearray(path.read_bytes())
    if data[:2] != b"MZ":
        raise ValueError(f"Not a PE image: {path}")
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        raise ValueError(f"Invalid PE image: {path}")
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic not in {0x10B, 0x20B}:
        raise ValueError("Unknown PE optional header")
    security = optional + (96 if magic == 0x10B else 112) + 32
    offset, size = struct.unpack_from("<II", data, security)
    data[optional + 64 : optional + 68] = b"\0" * 4
    data[security : security + 8] = b"\0" * 8
    if offset or size:
        if not offset or size < 8 or offset + size != len(data):
            raise ValueError("Certificate must be a bounded final PE overlay")
        del data[offset:]
    return bytes(data)


def assert_signature_only(original: Path, signed: Path):
    before, after = pe_content(original), pe_content(signed)
    if (
        not after.startswith(before)
        or len(after) - len(before) > 7
        or any(after[len(before) :])
    ):
        raise ValueError(f"Signed PE changed executable content: {original}")


def verify_payload(allow_overlay=False):
    base = read(WORK / "build-receipt.json")
    manifest_path = PAYLOAD / "payload-manifest.json"
    if digest(manifest_path) != base["payload_manifest_sha256"]:
        raise ValueError("Original payload manifest changed")
    manifest = read(manifest_path)
    overlay_path = PAYLOAD / "payload-signing-overlay.json"
    overlay = read(overlay_path) if allow_overlay and overlay_path.exists() else None
    overrides = {}
    if overlay:
        if "reused_signing" in overlay:
            provenance = overlay["reused_signing"]
            original = WORK / "native-reuse-original-signing-receipt.json"
            original_intake = WORK / "native-reuse-original-intake-manifest.json"
            if (
                digest(original) != provenance["original_receipt_sha256"]
                or digest(original_intake) != provenance["original_intake_sha256"]
            ):
                raise ValueError("Retained native signing reuse evidence changed")
            prior = read(original)
            if (
                prior["files"] != overlay["files"]
                or prior["original_payload_manifest_sha256"]
                != provenance["original_payload_manifest_sha256"]
            ):
                raise ValueError(
                    "Reused native signatures differ from their original receipt"
                )
            previous_intake = read(original_intake)
            identical_native_inputs(
                read(WORK / "native-signing-intake/manifest.json"), previous_intake
            )
            if previous_intake["original_payload_manifest_sha256"] != prior[
                "original_payload_manifest_sha256"
            ] or provenance["new_payload_manifest_sha256"] != digest(manifest_path):
                raise ValueError("Native reuse payload binding changed")
        if overlay["original_payload_manifest_sha256"] != digest(manifest_path):
            raise ValueError("Signing overlay names another payload")
        derived_seed = SEED["verify_derived"](PAYLOAD, overlay)
        if (
            overlay.get("derived_seed_manifest", {}).get("derived_sha256")
            != derived_seed
        ):
            raise ValueError("Derived seed manifest receipt hash mismatch")
        for item in overlay["files"]:
            if item["kind"] == "payload":
                if item["target"] in overrides:
                    raise ValueError("Duplicate overlay target")
                overrides[item["target"]] = item
    seen = set()
    for item in manifest:
        target = child(PAYLOAD, item["path"])
        seen.add(item["path"])
        if overlay and item["path"] == (SEED["RELATIVE"] / "manifest.json").as_posix():
            if overlay["derived_seed_manifest"]["original_sha256"] != item["sha256"]:
                raise ValueError("Original seed manifest identity mismatch")
            continue  # only this JSON is deterministically derived above
        signed = overrides.get(item["path"])
        if signed:
            if signed["unsigned_sha256"] != item["sha256"]:
                raise ValueError("Overlay original hash mismatch")
            original = child(WORK / "native-signing-intake", signed["path"])
            if digest(original) != item["sha256"]:
                raise ValueError("Retained original native file changed")
            assert_signature_only(original, target)
        expected = signed["signed_sha256"] if signed else item["sha256"]
        if digest(target) != expected:
            raise ValueError(f"Payload changed: {item['path']}")
    if set(overrides) - seen:
        raise ValueError("Overlay names an unrecorded payload file")
    extras = {"payload-manifest.json"}
    if overlay:
        extras.add("payload-signing-overlay.json")
    actual = {
        file.relative_to(PAYLOAD).as_posix()
        for file in PAYLOAD.rglob("*")
        if file.is_file()
    }
    if actual != seen | extras:
        raise ValueError("Payload contains missing or unrecorded files")
    wheels = list((WORK / "wheels").glob("*.whl"))
    if len(wheels) != 1 or digest(wheels[0]) != base["wheel_sha256"]:
        raise ValueError("Retained application wheel changed")
    return {
        "status": "verified",
        "original_files": len(manifest),
        "signed_payload_files": len(overrides),
    }


def prepare():
    verify_payload()
    root = WORK / "native-signing-intake"
    if root.exists():
        raise ValueError(
            "Signing intake already exists; preserve and inspect it before another attempt"
        )
    inventory = read(WORK / "native-pe-inventory.json")
    actual = {
        file.relative_to(PAYLOAD).as_posix()
        for file in PAYLOAD.rglob("*")
        if file.suffix.lower() in NATIVE
    }
    if actual != {item["path"] for item in inventory}:
        raise ValueError("Native inventory does not cover the actual payload")
    files = []
    for item in inventory:
        original = child(PAYLOAD, item["path"])
        if digest(original) != item["sha256"]:
            raise ValueError("Native inventory hash mismatch")
        if item["status"] == "Valid":
            continue  # preserve vendor signatures
        if item["status"] != "NotSigned":
            raise ValueError("Unexpected existing native signature status")
        path = "payload/" + item["path"]
        target = child(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        files.append(
            {
                "path": path,
                "target": item["path"],
                "kind": "payload",
                "sha256": digest(original),
                "size": original.stat().st_size,
            }
        )
    internal = list((WORK / "signed-uninstaller").glob("uninst-*.e*"))
    if not internal:
        raise ValueError("Prepare the stable internal Inno PE first")
    for original in internal:
        # The existing signing intake accepts .exe; its PE bytes are unmodified.
        path = "internal/" + original.with_suffix(".exe").name
        target = child(root, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(original, target)
        files.append(
            {
                "path": path,
                "target": original.name,
                "kind": "internal",
                "sha256": digest(original),
                "size": original.stat().st_size,
            }
        )
    base = read(WORK / "build-receipt.json")
    import subprocess

    manifest = {
        "schema": 1,
        "payload_source_commit": base["source_commit"],
        "packaging_commit": subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True
        ).strip(),
        "original_payload_manifest_sha256": base["payload_manifest_sha256"],
        "files": files,
    }
    write(root / "manifest.json", manifest)
    archive = REPO / "dist/windows/TownRecord-0.2.0-native-unsigned.zip"
    if archive.exists():
        raise ValueError("Signing intake archive already exists")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as output:
        for file in sorted(root.rglob("*")):
            if file.is_file():
                output.write(file, file.relative_to(root).as_posix())
    return {
        "archive": str(archive),
        "sha256": digest(archive),
        "members": len(files),
        "preserved_vendor_signed": sum(item["status"] == "Valid" for item in inventory),
    }


def identical_native_inputs(current, previous):
    def inventory(value):
        result = {item["path"]: item for item in value["files"]}
        if len(result) != len(value["files"]):
            raise ValueError("Duplicate native input identity")
        return result

    if inventory(current) != inventory(previous):
        raise ValueError(
            "Native signature reuse requires identical paths, kinds, targets, hashes and sizes"
        )


def validate_return(archive: Path, expected: str, previous_intake: Path | None = None):
    if digest(archive) != expected:
        raise ValueError("Signed return ZIP hash mismatch")
    intake = read(WORK / "native-signing-intake/manifest.json")
    signing_intake = read(previous_intake) if previous_intake else intake
    if previous_intake:
        identical_native_inputs(intake, signing_intake)
    root = WORK / "signed-native-return"
    if root.exists():
        raise ValueError("Signed return staging directory already exists")
    with zipfile.ZipFile(archive) as zipped:
        names = zipped.namelist()
        if len(names) != len({name.casefold() for name in names}):
            raise ValueError("Duplicate ZIP return entries")
        allowed = {item["path"] for item in intake["files"]} | {
            "manifest.json",
            "signing-receipt.json",
        }
        if set(names) != allowed:
            raise ValueError("Signed ZIP contains missing or unexpected entries")
        if any(entry.file_size > 512 * 1024**2 for entry in zipped.infolist()):
            raise ValueError("Signed ZIP member exceeds bound")
        if json.loads(zipped.read("manifest.json")) != signing_intake:
            raise ValueError("Signing workflow returned a different input manifest")
        receipt = json.loads(zipped.read("signing-receipt.json"))
        if (
            receipt["status"] != "verified_signed_native_payload"
            or receipt["original_payload_manifest_sha256"]
            != signing_intake["original_payload_manifest_sha256"]
        ):
            raise ValueError("Signing receipt does not identify this payload")
        result = {item["path"]: item for item in receipt["files"]}
        if len(result) != len(receipt["files"]) or set(result) != {
            item["path"] for item in intake["files"]
        }:
            raise ValueError("Signing receipt file coverage mismatch")
        for item in intake["files"]:
            signed = result[item["path"]]
            if (
                any(signed[key] != item[key] for key in ("kind", "target"))
                or signed["unsigned_sha256"] != item["sha256"]
            ):
                raise ValueError("Signing receipt original identity mismatch")
            target = child(root, item["path"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zipped.read(item["path"]))
            if (
                digest(target) != signed["signed_sha256"]
                or target.stat().st_size != signed["signed_size"]
            ):
                raise ValueError("Signed return file hash/size mismatch")
            original = child(WORK / "native-signing-intake", item["path"])
            if digest(original) != item["sha256"]:
                raise ValueError("Original signing intake changed")
            assert_signature_only(original, target)
        if previous_intake:
            original_receipt = WORK / "native-reuse-original-signing-receipt.json"
            original_manifest = WORK / "native-reuse-original-intake-manifest.json"
            original_receipt.write_bytes(zipped.read("signing-receipt.json"))
            original_manifest.write_bytes(zipped.read("manifest.json"))
            receipt["reused_signing"] = {
                "original_payload_manifest_sha256": signing_intake[
                    "original_payload_manifest_sha256"
                ],
                "original_receipt_sha256": digest(original_receipt),
                "original_intake_sha256": digest(original_manifest),
                "new_payload_manifest_sha256": intake[
                    "original_payload_manifest_sha256"
                ],
                "new_packaging_commit": intake["packaging_commit"],
                "rule": "all native input path, kind, target, SHA-256 and size identities are exactly unchanged",
            }
            receipt["original_payload_manifest_sha256"] = intake[
                "original_payload_manifest_sha256"
            ]
        write(root / "signing-receipt.json", receipt)
    return {
        "status": "signature_only_bytes_verified",
        "files": len(result),
        "staging": str(root),
    }


def apply():
    verify_payload()
    root = WORK / "signed-native-return"
    receipt = read(root / "signing-receipt.json")
    for item in receipt["files"]:
        source = child(root, item["path"])
        if digest(source) != item["signed_sha256"]:
            raise ValueError("Verified return changed before application")
    for item in receipt["files"]:
        source = child(root, item["path"])
        destination = child(
            PAYLOAD if item["kind"] == "payload" else WORK / "signed-uninstaller",
            item["target"],
        )
        shutil.copy2(source, destination)
    receipt["derived_seed_manifest"] = SEED["derive"](PAYLOAD, receipt)
    write(PAYLOAD / "payload-signing-overlay.json", receipt)
    return verify_payload(allow_overlay=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=["verify", "prepare", "validate-return", "apply"]
    )
    parser.add_argument("--allow-overlay", action="store_true")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--previous-intake", type=Path)
    args = parser.parse_args()
    if args.command == "verify":
        result = verify_payload(args.allow_overlay)
    elif args.command == "prepare":
        result = prepare()
    elif args.command == "validate-return":
        result = validate_return(
            args.archive, args.expected_sha256, args.previous_intake
        )
    else:
        result = apply()
    print(json.dumps(result))
