# benchlab/graph/runner.py

import logging
import types

from benchlab.core.datasource_manager import DataSourceManager
from .app import GraphApp

_logger = logging.getLogger("benchlab.graph")


def run_graph_mode(args=None):
    if args is None:
        args = types.SimpleNamespace(
            source="direct", interval=1.0,
            api_url="http://127.0.0.1:8000", api_port=8000,
            mqtt_broker="localhost", mqtt_port=1883,
        )

    source = args.source
    _logger.info(f"Graph: connecting via {source} datasource")

    ds_kwargs = {}
    if source in ("fastapi", "fastapi_custom"):
        ds_kwargs["base_url"] = args.api_url
    elif source == "mqtt":
        ds_kwargs["broker"] = args.mqtt_broker
        ds_kwargs["port"] = args.mqtt_port

    datasource = DataSourceManager(source_type=source, **ds_kwargs)
    if not datasource.connect():
        _logger.error(f"Graph: failed to connect to {source} datasource")
        return

    app = GraphApp(datasource=datasource)
    app.sensor_read_interval = args.interval
    try:
        app.run()
    except KeyboardInterrupt:
        pass
    finally:
        datasource.disconnect()
