"""Tests for firmware image loading/validation; no hardware needed."""
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


def test_load_image_elf_rejected(tmp_path):
    path = tmp_path / "fw.elf"
    path.write_bytes(b"\x7fELF")
    with pytest.raises(ValueError, match="debug symbols"):
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
