"""Non-hardware regression test for benchlab.vu.devices.

Regression test for issue #30: devices.py's module-level config load could
crash at import time if vu_server.config_template contained malformed JSON
-- the nested template-read try wasn't covered by the outer except. Extracted
into load_vu_server_config() so this is directly testable.
"""

from benchlab.vu.devices import load_vu_server_config, _DEFAULT_VU_CONFIG


def test_loads_existing_config(tmp_path):
    config_path = tmp_path / "vu_server.config"
    config_path.write_text(
        '{"vu_server_url": "http://localhost:9999", "api_key": "abc"}')

    result = load_vu_server_config(
        config_path, tmp_path / "unused_template.json")

    assert result["vu_server_url"] == "http://localhost:9999"
    assert result["api_key"] == "abc"


def test_creates_config_from_valid_template(tmp_path):
    config_path = tmp_path / "vu_server.config"
    template_path = tmp_path / "vu_server.config_template"
    template_path.write_text(
        '{"vu_server_url": "http://localhost:5340", "api_key": "", '
        '"logo_file": ""}')

    result = load_vu_server_config(config_path, template_path)

    assert result["vu_server_url"] == "http://localhost:5340"
    assert config_path.exists(), "config should be created from the template"


def test_falls_back_to_default_on_malformed_template(tmp_path):
    """Regression test: a malformed template used to crash import of
    devices.py with an unhandled json.JSONDecodeError."""
    config_path = tmp_path / "vu_server.config"
    template_path = tmp_path / "vu_server.config_template"
    template_path.write_text("{ not valid json")

    result = load_vu_server_config(config_path, template_path)

    assert result == _DEFAULT_VU_CONFIG


def test_falls_back_to_default_when_no_config_or_template(tmp_path):
    config_path = tmp_path / "vu_server.config"
    template_path = tmp_path / "does_not_exist.json"

    result = load_vu_server_config(config_path, template_path)

    assert result == _DEFAULT_VU_CONFIG
    assert config_path.exists()


def test_falls_back_to_default_on_malformed_existing_config(tmp_path):
    config_path = tmp_path / "vu_server.config"
    config_path.write_text("{ not valid json")

    result = load_vu_server_config(
        config_path, tmp_path / "unused_template.json")

    assert result == _DEFAULT_VU_CONFIG
