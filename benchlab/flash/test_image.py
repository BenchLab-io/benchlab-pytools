"""Tests for firmware image loading/validation; no hardware needed."""
import struct

import pytest

from benchlab_pycore.core import (
    BENCHLAB_BL2_PRODUCT_ID,
    BENCHLAB_ORIGINAL_PRODUCT_ID,
)

from benchlab.flash import image


def test_load_image_bin(tmp_path):
    path = tmp_path / "fw.bin"
    path.write_bytes(b"\x01\x02\x03\x04")
    assert image.load_image(path) == b"\x01\x02\x03\x04"


def test_load_image_hex(tmp_path):
    path = tmp_path / "fw.hex"
    path.write_text(
        ":04000000DEADBEEFC4\n"
        ":00000001FF\n"
    )
    data = image.load_image(path)
    assert data == b"\xDE\xAD\xBE\xEF"


def _build_minimal_elf32(segments):
    """Build a minimal valid 32-bit little-endian ELF (EM_ARM) with the
    given PT_LOAD segments as (paddr, data) tuples -- just enough for
    pyelftools to parse, to test this module's PT_LOAD extraction/
    flattening logic against a controlled input without needing a real
    toolchain-produced firmware ELF."""
    ehsize = 52
    phentsize = 32
    phnum = len(segments)
    phoff = ehsize
    data_start = phoff + phentsize * phnum

    ph_bytes = b""
    file_data = b""
    offset = data_start
    for paddr, data in segments:
        ph_bytes += struct.pack(
            "<8I",
            1,            # p_type = PT_LOAD
            offset,       # p_offset
            paddr,        # p_vaddr
            paddr,        # p_paddr
            len(data),    # p_filesz
            len(data),    # p_memsz
            5,            # p_flags R+X
            4,            # p_align
        )
        file_data += data
        offset += len(data)

    e_ident = b"\x7fELF" + bytes([1, 1, 1, 0]) + b"\x00" * 8
    header = e_ident + struct.pack(
        "<HHIIIIIHHHHHH",
        2, 0x28, 1, 0, phoff, 0, 0, ehsize, phentsize, phnum, 0, 0, 0)
    assert len(header) == ehsize
    return header + ph_bytes + file_data


def test_load_image_elf_extracts_single_segment(tmp_path):
    path = tmp_path / "fw.elf"
    path.write_bytes(_build_minimal_elf32([
        (image.FLASH_BASE, b"\x01\x02\x03\x04"),
    ]))
    assert image.load_image(path) == b"\x01\x02\x03\x04"


def test_load_image_elf_fills_gaps_between_segments_with_0xff(tmp_path):
    path = tmp_path / "fw.elf"
    path.write_bytes(_build_minimal_elf32([
        (image.FLASH_BASE, b"\x11" * 16),
        (image.FLASH_BASE + 0x20, b"\x22" * 8),
    ]))
    data = image.load_image(path)
    assert data == b"\x11" * 16 + b"\xff" * 16 + b"\x22" * 8


def test_load_image_elf_rejects_segment_outside_flash_range(tmp_path):
    path = tmp_path / "fw.elf"
    path.write_bytes(_build_minimal_elf32([
        (0x20000000, b"\x01\x02\x03\x04"),  # SRAM, not flash
    ]))
    with pytest.raises(ValueError, match="outside the internal flash"):
        image.load_image(path)


def test_load_image_elf_rejects_no_loadable_segments(tmp_path):
    path = tmp_path / "fw.elf"
    path.write_bytes(_build_minimal_elf32([]))
    with pytest.raises(ValueError, match="no loadable"):
        image.load_image(path)


def test_load_image_unsupported_extension(tmp_path):
    path = tmp_path / "fw.txt"
    path.write_text("nope")
    with pytest.raises(ValueError, match="unsupported"):
        image.load_image(path)


@pytest.mark.parametrize("product_id,ok_size,bad_size", [
    (BENCHLAB_ORIGINAL_PRODUCT_ID,
     image.BENCHLAB1_FLASH_SIZE,
     image.BENCHLAB1_FLASH_SIZE + 1),
    (BENCHLAB_BL2_PRODUCT_ID,
     image.BENCHLAB2_USABLE_FLASH_SIZE,
     image.BENCHLAB2_USABLE_FLASH_SIZE + 1),
])
def test_validate_image_size_boundaries(product_id, ok_size, bad_size):
    image.validate_image_size(b"\x00" * ok_size, product_id)
    with pytest.raises(ValueError):
        image.validate_image_size(b"\x00" * bad_size, product_id)


def test_validate_image_size_unknown_product():
    with pytest.raises(ValueError, match="Unknown BENCHLAB product_id"):
        image.validate_image_size(b"\x00", 0x99)


@pytest.mark.parametrize("filename,expected", [
    ("benchlab-original-fw06-v0.6.0-56de794.bin",
     BENCHLAB_ORIGINAL_PRODUCT_ID),
    ("benchlab2-fw07-v0.7.1-f8946fb.bin", BENCHLAB_BL2_PRODUCT_ID),
    ("my_custom_build.bin", None),
])
def test_guess_product_from_filename(filename, expected):
    assert image.guess_product_from_filename(filename) == expected


def test_confirm_image_matches_device_match():
    ok, msg = image.confirm_image_matches_device(
        "benchlab-original-fw06-v0.6.0-56de794.bin",
        BENCHLAB_ORIGINAL_PRODUCT_ID)
    assert ok
    assert msg == ""


def test_confirm_image_matches_device_mismatch():
    ok, msg = image.confirm_image_matches_device(
        "benchlab-original-fw06-v0.6.0-56de794.bin", BENCHLAB_BL2_PRODUCT_ID)
    assert not ok
    assert "benchlab1" in msg
    assert "benchlab2" in msg


def test_confirm_image_matches_device_unknown_filename_is_soft_warning():
    ok, msg = image.confirm_image_matches_device(
        "custom_build.bin", BENCHLAB_ORIGINAL_PRODUCT_ID)
    assert ok
    assert "Could not determine" in msg
