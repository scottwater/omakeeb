"""Archive a manufacturer's local firmware file without flashing or probing USB.

A saved firmware *file* is not a readback of installed firmware. Archives record
that distinction and do not assert compatibility with a connected keyboard.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

MAX_FIRMWARE_BYTES = 64 * 1024 * 1024


class FirmwareArchiveError(ValueError):
    pass


def archive_firmware(source, directory=None, identity=None) -> Path:
    """Copy a local .bin/.hex/.uf2/.zip, hash it, and return its JSON manifest.

    Neither content nor extension is sufficient to determine flash compatibility.
    No file in the archive is ever executed, extracted or sent to a device.
    """
    from .safety import default_backup_dir
    source = Path(source)
    suffix = source.suffix.lower()
    if suffix not in (".bin", ".hex", ".uf2", ".zip"):
        raise FirmwareArchiveError("Choose a manufacturer's .bin, .hex, .uf2 or .zip firmware file.")
    directory = Path(directory) if directory is not None else default_backup_dir() / "firmware"
    temporary = None
    manifest_temporary = None
    try:
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if directory.stat().st_mode & 0o077:
            raise FirmwareArchiveError(f"Firmware archive directory must be private (chmod 700): {directory}")
        digest = hashlib.sha256()
        size = 0
        # O_NONBLOCK prevents FIFOs/device paths from hanging a GUI worker.
        fd = os.open(source, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise FirmwareArchiveError("Firmware source must be a regular local file.")
            output_fd, temporary = tempfile.mkstemp(prefix=".firmware-", dir=directory)
            with os.fdopen(output_fd, "wb") as target:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_FIRMWARE_BYTES:
                        raise FirmwareArchiveError("Firmware file exceeds the 64 MiB archive limit.")
                    target.write(chunk)
                    digest.update(chunk)
                if size == 0:
                    raise FirmwareArchiveError("Firmware file is empty.")
                target.flush()
                os.fsync(target.fileno())
        checksum = digest.hexdigest()
        name = f"{checksum}{suffix}"
        destination = directory / name
        # Identical content shares a filename. Atomic replacement repairs an
        # existing damaged copy without following a preexisting symlink.
        os.replace(temporary, destination)
        temporary = None
        manifest = {
            "format": "omakeeb-firmware-archive", "version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_name": source.name, "filename": name,
            "sha256": checksum, "bytes": size,
            "read_from_keyboard": False, "compatibility_verified": False,
            "identity": {key: identity[key] for key in ("vendor_id", "product_id", "product", "serial") if identity and key in identity},
        }
        manifest_fd, manifest_temporary = tempfile.mkstemp(prefix=".manifest-", dir=directory)
        with os.fdopen(manifest_fd, "w") as stream:
            json.dump(manifest, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        manifest_path = directory / f"{checksum[:16]}-{uuid.uuid4().hex[:8]}.json"
        os.replace(manifest_temporary, manifest_path)
        manifest_temporary = None
        directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return manifest_path
    except OSError as exc:
        raise FirmwareArchiveError(f"Could not archive firmware: {exc}") from exc
    finally:
        for path in (temporary, manifest_temporary):
            if path is not None:
                try:
                    os.unlink(path)
                except OSError:
                    pass


def verify_archive(manifest_path) -> dict:
    """Verify the saved copy's length and digest, without opening external paths."""
    manifest_path = Path(manifest_path)
    try:
        if manifest_path.stat().st_size > 16384:
            raise FirmwareArchiveError("Invalid firmware manifest size.")
        manifest = json.loads(manifest_path.read_text())
        if not isinstance(manifest, dict) or manifest.get("format") != "omakeeb-firmware-archive" or manifest.get("version") != 1:
            raise FirmwareArchiveError("Unsupported firmware archive.")
        name = manifest.get("filename")
        if not isinstance(name, str) or Path(name).name != name or name in ("", ".", ".."):
            raise FirmwareArchiveError("Invalid archive filename.")
        firmware = manifest_path.parent / name
        fd = os.open(firmware, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        digest, size = hashlib.sha256(), 0
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise FirmwareArchiveError("Archive data is not a regular file.")
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_FIRMWARE_BYTES:
                    raise FirmwareArchiveError("Firmware archive exceeds the size limit.")
                digest.update(chunk)
        if size != manifest.get("bytes") or digest.hexdigest() != manifest.get("sha256"):
            raise FirmwareArchiveError("Firmware archive checksum or size does not match.")
        return manifest
    except (OSError, ValueError, UnicodeError) as exc:
        if isinstance(exc, FirmwareArchiveError):
            raise
        raise FirmwareArchiveError(f"Could not verify firmware archive: {exc}") from exc
