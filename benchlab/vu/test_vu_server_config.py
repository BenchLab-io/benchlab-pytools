"""Non-hardware regression test for benchlab.vu.vu_server_config.

Regression test for issue #30: update_vu_config used to raise KeyError on
a mapping entry missing "dial_uid" (direct dict indexing instead of .get()),
aborting the whole config save.
"""

import json

from benchlab.vu import vu_server_config


def test_update_vu_config_tolerates_mapping_without_dial_uid(
        tmp_path, monkeypatch):
    config_path = tmp_path / "vu_server.config"
    config_path.write_text(json.dumps({
        "vu_server_url": "http://localhost:5340",
        "api_key": "",
        "mappings": [
            # malformed/partial entry
            {"benchlab_uid": "legacy-entry-missing-dial-uid"},
            {"dial_uid": "DIAL-1", "dial_name": "Old"},
        ],
        "update_interval_sec": 1,
    }))
    monkeypatch.setattr(vu_server_config, "config_path", config_path)

    vu_server_config.update_vu_config(
        dial_uid="DIAL-2",
        dial_name="New Dial",
        device={"uid": "BL-UID", "port": "COM3"},
        sensor="CPU_Temp",
    )

    result = json.loads(config_path.read_text())
    dial_uids = [m.get("dial_uid") for m in result["mappings"]]
    assert "DIAL-2" in dial_uids
    assert "DIAL-1" in dial_uids
    # The malformed entry (no dial_uid) survives untouched rather than
    # crashing the whole save.
    assert any(
        "benchlab_uid" in m and "dial_uid" not in m
        for m in result["mappings"])
