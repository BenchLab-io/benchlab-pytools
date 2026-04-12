# benchlab/graph/runner.py

import logging
import types

from benchlab.core.datasource_manager import DataSourceManager
from .app import GraphApp

_logger = logging.getLogger("benchlab.graph")


def run_graph_mode(args=None):
    """Launch the DearPyGui graph interface.

    Parameters
    ----------
    args:
        Standard benchlab args namespace with fields:
            source      – "direct" | "fastapi" | "mqtt"
            interval    – sensor poll interval in seconds
            api_url     – FastAPI base URL (fastapi source)
            api_port    – FastAPI port (fastapi source)
            mqtt_broker – MQTT broker host (mqtt source)
            mqtt_port   – MQTT broker port (mqtt source)

        If None, defaults to direct mode (backwards-compatible).
    """
    if args is None:
        args = types.SimpleNamespace(
            source="direct",
            interval=1.0,
            api_url="http://127.0.0.1:8000",
            api_port=8000,
            mqtt_broker="localhost",
            mqtt_port=1883,
        )

    source = args.source

    # Build a DataSourceManager for the requested source and pass it to the
    # app. GraphApp.device already knows how to use a datasource via
    # _run_datasource_loop; direct mode falls back to serial as before.
    if source == "direct":
        datasource = None   # GraphApp uses its own serial loop for direct
    else:
        _logger.info(f"Graph: connecting via {source} datasource")
        datasource = DataSourceManager(source_type=source)

        kwargs = {}
        if source == "fastapi":
            kwargs["base_url"] = args.api_url
        elif source == "mqtt":
            kwargs["broker"] = args.mqtt_broker
            kwargs["port"] = args.mqtt_port

        if not datasource.connect(**kwargs):
            _logger.error(f"Graph: failed to connect to {source} datasource")
            return

    app = GraphApp(datasource=datasource)
    app.sensor_read_interval = args.interval

    try:
        app.run()
    finally:
        if datasource is not None:
            datasource.disconnect()