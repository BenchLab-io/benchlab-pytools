"""Non-hardware regression tests for benchlab.config.diff.

Pure logic tests -- no device, no I/O. Covers compute_diff() correctly
identifying changed/unchanged fields across device name, fan profiles, RGB
profiles, and calibration, and format_diff()'s rendering including the
"no changes" case.
"""

from benchlab.config.schema import (
    DeviceConfig, DeviceSelector, FanProfile, FanConfig, RGBConfig,
)
from benchlab.config.diff import compute_diff, format_diff


def _selector():
    return DeviceSelector(type="any")


def _empty_state():
    return {
        "deviceName": None,
        "fanProfiles": None,
        "rgbProfiles": None,
        "calibration": None,
        "readErrors": [],
    }


def test_no_changes_when_desired_matches_current():
    current = {
        "deviceName": "SameName",
        "fanProfiles": None,
        "rgbProfiles": None,
        "calibration": None,
        "readErrors": [],
    }
    desired = DeviceConfig(selector=_selector(), deviceName="SameName")

    diff = compute_diff(current, desired)

    assert diff.is_empty()
    assert "No changes" in format_diff(diff, "COM4")


def test_device_name_change_detected():
    current = {**_empty_state(), "deviceName": "OldName"}
    desired = DeviceConfig(selector=_selector(), deviceName="NewName")

    diff = compute_diff(current, desired)

    assert not diff.is_empty()
    assert diff.device_name_change is not None
    assert diff.device_name_change.current == "OldName"
    assert diff.device_name_change.desired == "NewName"


def test_device_name_unset_in_desired_is_not_a_change():
    current = {**_empty_state(), "deviceName": "ExistingName"}
    desired = DeviceConfig(selector=_selector())  # deviceName defaults to None

    diff = compute_diff(current, desired)

    assert diff.device_name_change is None


def test_fan_config_change_detected_per_fan():
    current = {
        **_empty_state(),
        "fanProfiles": [{
            "profileId": 0,
            "fans": [{
                "fanId": 0, "FanMode": 0, "TempSource": 0, "Temp": [300, 600],
                "Duty": [30, 80], "RampStep": 5, "FixedDuty": 50,
                "MinDuty": 20, "MaxDuty": 100, "FanStop": 0,
            }],
        }],
    }
    desired = DeviceConfig(
        selector=_selector(),
        fanProfiles=[FanProfile(profileId=0, fans=[
            FanConfig(fanId=0, FanMode=1, TempSource=0, Duty=[40, 90]),
        ])],
    )

    diff = compute_diff(current, desired)

    assert (0, 0) in diff.fan_changes
    changed_fields = {c.field for c in diff.fan_changes[(0, 0)]}
    assert "FanMode" in changed_fields
    assert "Duty" in changed_fields
    assert "TempSource" not in changed_fields  # unchanged, should not appear


def test_fan_config_new_fan_shows_full_diff():
    """A fan present in desired but absent from current (fresh device /
    never-configured profile) should show as a change, not error."""
    current = _empty_state()
    desired = DeviceConfig(
        selector=_selector(),
        fanProfiles=[
            FanProfile(
                profileId=0,
                fans=[
                    FanConfig(
                        fanId=0,
                        FanMode=1,
                        TempSource=0)])],
    )

    diff = compute_diff(current, desired)

    assert (0, 0) in diff.fan_changes


def test_rgb_config_change_detected():
    current = {**_empty_state(),
               "rgbProfiles": [{"profileId": 0,
                                "Mode": 9,
                                "Red": 255,
                                "Green": 0,
                                "Blue": 0,
                                "Direction": 0,
                                "Speed": 50}],
               }
    desired = DeviceConfig(
        selector=_selector(),
        rgbProfiles=[
            RGBConfig(
                profileId=0,
                Mode=5,
                Red=0,
                Green=0,
                Blue=255,
                Direction=0,
                Speed=50)],
    )

    diff = compute_diff(current, desired)

    assert 0 in diff.rgb_changes
    changed_fields = {c.field for c in diff.rgb_changes[0]}
    assert "Mode" in changed_fields
    assert "Blue" in changed_fields
    assert "Green" not in changed_fields  # unchanged


def test_calibration_change_detected_as_whole_blob():
    current = {**_empty_state(), "calibration": {"Crc": 1}}
    desired = DeviceConfig(selector=_selector(), calibration={"Crc": 2})

    diff = compute_diff(current, desired)

    assert diff.calibration_changed is True


def test_calibration_unset_in_desired_is_not_a_change():
    current = {**_empty_state(), "calibration": {"Crc": 1}}
    # calibration defaults to None
    desired = DeviceConfig(selector=_selector())

    diff = compute_diff(current, desired)

    assert diff.calibration_changed is False


def test_read_errors_propagated_to_diff_and_format():
    current = {**_empty_state(), "readErrors": ["calibration: timeout"]}
    desired = DeviceConfig(selector=_selector())

    diff = compute_diff(current, desired)
    text = format_diff(diff, "COM4")

    assert diff.read_errors == ["calibration: timeout"]
    assert "calibration: timeout" in text
    assert "Warning" in text


def test_format_diff_renders_fan_and_rgb_sections():
    current = _empty_state()
    desired = DeviceConfig(
        selector=_selector(),
        deviceName="NewName",
        fanProfiles=[
            FanProfile(
                profileId=1,
                fans=[
                    FanConfig(
                        fanId=2,
                        FanMode=1,
                        TempSource=0)])],
        rgbProfiles=[
            RGBConfig(
                profileId=0,
                Mode=5,
                Red=1,
                Green=2,
                Blue=3,
                Direction=0,
                Speed=10)],
    )

    diff = compute_diff(current, desired)
    text = format_diff(diff, "COM4 - Test")

    assert "COM4 - Test" in text
    assert "Fan 2 Profile 1" in text
    assert "RGB Profile 0" in text
    assert "Device Name" in text
