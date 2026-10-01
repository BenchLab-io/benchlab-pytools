"""Firmware image loading and safety validation for the flash tool."""

import logging
import re
from pathlib import Path
from typing import Optional, Tuple

from benchlab_pycore.core import (
    BENCHLAB_BL2_PRODUCT_ID,
    BENCHLAB_ORIGINAL_PRODUCT_ID,
)

logger = logging.getLogger("benchlab.flash.image")

FLASH_BASE = 0x08000000
PAGE_SIZE = 2048  # STM32F303xB/C flash page size.

BENCHLAB1_FLASH_SIZE = 128 * 1024
BENCHLAB2_EEPROM_START = 0x0801E000
BENCHLAB2_USABLE_FLASH_SIZE = 120 * 1024

_PRODUCT_NAMES = {
    BENCHLAB_ORIGINAL_PRODUCT_ID: "benchlab1",
    BENCHLAB_BL2_PRODUCT_ID: "benchlab2",
}

# Matches release artifact names produced by the firmware repo's
# tools/release_name.py. BL1 uses "original" as its variant slug, not "1" --
# e.g. "benchlab-original-fw06-v0.6.0-56de794.bin" vs
# "benchlab2-fw07-v0.7.1-f8946fb.bin" (confirmed against actual
# BenchLab-io/BENCHLAB-FW release assets).
_RELEASE_NAME_RE = re.compile(
    r"benchlab-?(?P<variant>original|1|2)-fw\d+-v[\d.]+", re.IGNORECASE)


def load_image(path: Path) -> bytes:
    """Load a firmware image file, dispatching on its extension.

    .bin is read raw (the primary supported format -- what release
    artifacts ship). .hex is converted via intelhex. .elf is rejected,
    since it carries debug symbols/sections rather than a flat memory
    image -- release artifacts ship a sibling .bin/.hex for the same build.
    """
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".bin":
        return path.read_bytes()

    if suffix == ".hex":
        from intelhex import IntelHex
        return IntelHex(str(path)).tobinstr()

    if suffix == ".elf":
        raise ValueError(
            f"{path}: .elf images can't be flashed directly (they contain "
            "debug symbols/sections, not a flat memory image). Use the "
            ".bin or .hex file from the same release instead.")

    raise ValueError(
        f"{path}: unsupported firmware image extension '{suffix}' "
        "(expected .bin or .hex)")


def validate_image_size(data: bytes, product_id: int) -> None:
    """Raise ValueError if flashing `data` at FLASH_BASE would write past
    the target variant's usable flash -- in particular, past BENCHLAB2's
    calibration EEPROM-in-flash region at BENCHLAB2_EEPROM_START."""
    if product_id == BENCHLAB_BL2_PRODUCT_ID:
        limit = BENCHLAB2_USABLE_FLASH_SIZE
        reason = (
            f"would overwrite the calibration EEPROM region starting at "
            f"0x{BENCHLAB2_EEPROM_START:08X}")
    elif product_id == BENCHLAB_ORIGINAL_PRODUCT_ID:
        limit = BENCHLAB1_FLASH_SIZE
        reason = "exceeds the device's flash size"
    else:
        raise ValueError(f"Unknown BENCHLAB product_id: 0x{product_id:02X}")

    if len(data) > limit:
        raise ValueError(
            f"Image is {len(data)} bytes, which {reason} "
            f"(limit: {limit} bytes)")


def guess_product_from_filename(path: Path) -> Optional[int]:
    """Best-effort guess of the intended product_id from a release-style
    filename. Returns None if the filename doesn't match the convention
    (e.g. the user renamed the file) -- callers should treat that as an
    unknown, not a mismatch."""
    match = _RELEASE_NAME_RE.search(Path(path).name)
    if not match:
        return None
    variant = match.group("variant").lower()
    return (
        BENCHLAB_ORIGINAL_PRODUCT_ID if variant in ("1", "original")
        else BENCHLAB_BL2_PRODUCT_ID)


def confirm_image_matches_device(
        path: Path, device_product_id: int) -> Tuple[bool, str]:
    """Compare a filename-derived product guess against the connected
    device's actual product_id.

    Returns (ok, message):
      - (True, "") if the filename matches the device, or no guess is
        possible (not a hard match, but nothing to block on either).
      - (False, message) if the filename clearly indicates a different
        variant than what's connected -- a hard mismatch that callers
        should block on without explicit override.
    """
    guessed = guess_product_from_filename(path)
    device_name = _PRODUCT_NAMES.get(
        device_product_id, f"0x{device_product_id:02X}")

    if guessed is None:
        return True, (
            f"Could not determine the firmware variant from the filename "
            f"'{Path(path).name}'. Connected device reports: {device_name}. "
            "Make sure this image is built for the connected device.")

    if guessed != device_product_id:
        guessed_name = _PRODUCT_NAMES.get(guessed, f"0x{guessed:02X}")
        return False, (
            f"Image filename '{Path(path).name}' indicates {guessed_name} "
            f"firmware, but the connected device reports {device_name}. "
            "Refusing to flash a mismatched image.")

    return True, ""
