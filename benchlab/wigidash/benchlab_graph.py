# benchlab_graph.py

from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

import io
import matplotlib
matplotlib.use("Agg") 
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os
import signal
import time

from benchlab.wigidash.benchlab_ui import load_fonts, draw_header, draw_footer, bind_button, load_logo, UIButton, UITheme, BUTTON_DEFS
from benchlab.wigidash.benchlab_utils import display_image, get_logger


logger = get_logger("BenchlabGraph")


class BenchlabGraph:
    SCREEN_WIDTH = UITheme.SCREEN_WIDTH
    SCREEN_HEIGHT = UITheme.SCREEN_HEIGHT
    HEADER_HEIGHT = UITheme.HEADER_HEIGHT
    FOOTER_HEIGHT = UITheme.FOOTER_HEIGHT
    PADDING = UITheme.PADDING
    COLOR_SECTION = UITheme.COLOR_SECTION
    COLOR_TEXT = UITheme.COLOR_TEXT

    NON_PLOT_METRICS = {
        "Fan1_Status", "Fan2_Status", "Fan3_Status", "Fan4_Status",
        "Fan5_Status", "Fan6_Status", "Fan7_Status", "Fan8_Status",
        "Fan9_Status", "FanExtDuty", "Fans"
    }

    def __init__(self, wigidash, wigi, metrics, telemetry_history=None, telemetry_context=None, manager=None):
        self.wigidash = wigidash
        self.wigi = wigi
        self.metrics = metrics
        self.history = telemetry_history or wigi.history
        self.telemetry_context = telemetry_context
        self.manager = manager

        self.last_touch_time = 0
        self.all_metrics = list(wigi.sensor_data.keys())
        self.selected_metrics = metrics.copy()
        self.running = False

        self.ui_fonts = load_fonts()
        self.ui_logo = load_logo()

        self.footer_btn_config = []
        self.metric_btn_config = []


    # -------------------------------
    # Page Lifecycle
    # -------------------------------

    def start(self):
        self.running = True
        logger.info(f"Graph page started with metrics: {self.selected_metrics}")

    def return_to_overview(self):
        logger.info("Returning to Overview page")
        self.running = False
        if self.wigi.overview_page is None:
            self.wigi.overview_page = self.wigi.create_overview_page()
        self.wigi.next_page = "overview"


    # -------------------------------
    # Touch Handling
    # -------------------------------

    def check_touch(self, touch):
        if not getattr(self, "running", False):
            return

        now = int(time.monotonic() * 1000)
        if now - getattr(self, "last_touch_time", 0) < 0.1:
            return

        if touch is None or getattr(touch, "Type", 0) == 0:
            return

        x, y = getattr(touch, "X", 0), getattr(touch, "Y", 0)

        # Footer buttons
        for btn in self.footer_btn_config:
            if btn["x0"] <= x <= btn["x1"] and btn["y0"] <= y <= btn["y1"]:
                logger.info(f"Footer button pressed: {btn['text']}")
                if btn.get("callback"):
                    btn["callback"]()
                self.last_touch_time = now
                return

        # Metric buttons (toggle for plotting)
        for btn in self.metric_btn_config:
            if btn["x0"] <= x <= btn["x1"] and btn["y0"] <= y <= btn["y1"]:
                # Metric toggle button
                if "metric" in btn:
                    metric = btn["metric"]
                    if not hasattr(self, "plot_metrics"):
                        self.plot_metrics = self.selected_metrics.copy()
                    if metric in self.plot_metrics:
                        self.plot_metrics.remove(metric)
                    else:
                        self.plot_metrics.append(metric)
                # Callback button (e.g., All Duty / All RPM)
                elif "callback" in btn and btn["callback"]:
                    btn["callback"]()
                self.last_touch_time = now
                return

    # -------------------------------
    # Helpers
    # -------------------------------

    def toggle_all_fan_metrics(self, suffix):
        """Selects/deselects all metrics ending with the given suffix (_Duty or _RPM)."""
        # Check if all metrics of this type are already selected
        all_selected = all(
            m in self.plot_metrics
            for m in self.selected_metrics
            if m.endswith(suffix)
        )

        # Toggle: if all selected, deselect all; otherwise, select all
        for m in self.selected_metrics:
            if m.endswith(suffix):
                if all_selected:
                    if m in self.plot_metrics:
                        self.plot_metrics.remove(m)
                else:
                    if m not in self.plot_metrics:
                        self.plot_metrics.append(m)

    def select_rail_section_metrics(self, key_suffix):
        """Select all metrics for a given section (Power, Current, Voltage)."""
        all_rails = ['EPS1','EPS2','12V','5V','5VSB','3.3V','PCIE8_1','PCIE8_2','PCIE8_3','HPWR1','HPWR2']
        metrics = [f"{r}_{key_suffix}" for r in all_rails 
            if f"{r}_{key_suffix}" in self.wigi.sensor_data]
        self.plot_metrics = metrics
        logger.info(f"Graph metrics updated for section '{key_suffix}': {self.plot_metrics}")


    # -------------------------------
    # Rendering
    # -------------------------------

    def render_and_display(self):
        """Wrapper for launcher compatibility"""
        self.render_graph()

    def render_graph(self):
        img = Image.new('RGB', (self.SCREEN_WIDTH, self.SCREEN_HEIGHT), color=(0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Use class attributes from UITheme
        padding = self.PADDING
        header_height = self.HEADER_HEIGHT
        footer_height = self.FOOTER_HEIGHT

        title_spacing = 2
        line_height = 18
        data = self.wigi.sensor_data or {}

        self.padding = padding
        self.header_height = header_height

        total_top_width = self.SCREEN_WIDTH - 4 * padding
        self.col1_width = self.col2_width = int(total_top_width * 0.25) - 3
        self.col3_width = total_top_width - self.col1_width - self.col2_width - 1

        self.x1 = padding
        self.x2 = self.x1 + self.col1_width + padding
        self.x3 = self.x2 + self.col2_width + padding

        # ---- Helper: draw rounded card ----
        def rounded_card(x, y, w, h, title):
            draw.rounded_rectangle([x, y, x+w, y+h], radius=12, outline=(200,200,200), width=1, fill=(15,15,15))
            draw.text((x+8, y+8),
                title,
                fill=self.COLOR_SECTION,
                font=self.ui_fonts["title"])

        # ---- Initialize plot_metrics if missing ----
        if not hasattr(self, "plot_metrics"):
            self.plot_metrics = self.selected_metrics.copy()

        # ---- Header ----
        draw_header(draw, img, self.ui_fonts, "BENCHLAB TELEMETRY HISTORY", self.ui_logo)

        # ---- Footer ----
        btns = [
            bind_button(UIButton.SHUTDOWN, self.manager.graceful_shutdown),
            bind_button(UIButton.OVERVIEW, self.return_to_overview),
        ]
        # Safely determine context for port/UID
        ctx = getattr(self, "telemetry_context", None)

        if ctx:
            port = getattr(ctx, "port", "N/A")
            uid = getattr(ctx, "uid", "N/A")
            device_info = getattr(ctx, "device_info", {})
            vid = device_info.get("VendorId", 0)
            pid = device_info.get("ProductId", 0)
            fw = device_info.get("FwVersion", 0)

            info_parts = [
                f"Port: {port}",
                f"Vendor ID: 0x{vid:02X}",
                f"Product ID: 0x{pid:02X}",
                f"FW: 0x{fw:02X}",
                f"UID: {uid or 'N/A'}"
            ]
            info_line = " | ".join(info_parts)
        else:
            info_line = "Port: N/A | UID: N/A"

        # Draw footer using correct fonts
        self.footer_btn_config = draw_footer(draw, self.ui_fonts, info_line, btns)

        # ---- Left panel: metrics ----
        panel_width = 200
        btn_h = 25
        panel_x = padding
        panel_y = header_height + padding
        self.metric_btn_config = []

        draw.text((panel_x+5, panel_y), 
            "Metrics", 
            fill=self.COLOR_SECTION,
            font=self.ui_fonts["title"])
        panel_y += self.ui_fonts["title"].getbbox("Metrics")[3] + 8

        METRIC_BUTTON_COLOR = BUTTON_DEFS.get(UIButton.GRAPH_METRIC, {}).get("color", (0, 0, 0))

        # Split numeric metrics into fan pairs and others
        fan_pairs = []
        other_metrics = []

        for m in self.selected_metrics:
            if m in BenchlabGraph.NON_PLOT_METRICS:
                continue
            if "_Duty" in m:
                fan_num = m.split("_")[0]  # e.g., "Fan1"
                rpm_metric = fan_num + "_RPM"
                if rpm_metric in self.selected_metrics:
                    fan_pairs.append((m, rpm_metric))
            elif "_RPM" not in m:
                other_metrics.append(m)  # non-fan numeric metrics

        # ---- Draw fan pairs first ----
        half_width = panel_width // 2 - 4  # 4px spacing between Duty and RPM
        spacing = 4
        for duty_m, rpm_m in fan_pairs:
            y0 = panel_y
            y1 = y0 + btn_h

            # Left half: Duty
            color = (252, 228, 119) if duty_m in self.plot_metrics else (50,50,50)
            text_color = BUTTON_DEFS.get(UIButton.GRAPH_METRIC, {}).get("color", (0,0,0)) if duty_m in self.plot_metrics else self.COLOR_TEXT
            draw.rectangle([panel_x, y0, panel_x+half_width, y1], fill=color, outline=(200,200,200))
            draw.text((panel_x+5, y0+6), 
                duty_m, 
                fill=text_color,
                font=self.ui_fonts["text"])
            self.metric_btn_config.append({
                "x0": panel_x, "y0": y0, "x1": panel_x+half_width, "y1": y1,
                "metric": duty_m
            })

            # Right half: RPM
            x_start = panel_x + half_width + spacing
            color = (252, 228, 119) if rpm_m in self.plot_metrics else (50,50,50)
            text_color = BUTTON_DEFS.get(UIButton.GRAPH_METRIC, {}).get("color", (0,0,0)) if rpm_m in self.plot_metrics else self.COLOR_TEXT
            draw.rectangle([x_start, y0, panel_x+panel_width, y1], fill=color, outline=(200,200,200))
            draw.text((x_start+5, y0+6), 
                rpm_m, 
                fill=text_color,
                font=self.ui_fonts["text"])
            self.metric_btn_config.append({
                "x0": x_start, "y0": y0, "x1": panel_x+panel_width, "y1": y1,
                "metric": rpm_m
            })

            panel_y += btn_h + 4  # next row

        # ---- Draw other numeric metrics ----
        for metric in other_metrics:
            y0 = panel_y
            y1 = y0 + btn_h
            color = (252, 228, 119) if metric in self.plot_metrics else (50,50,50)
            text_color = BUTTON_DEFS.get(UIButton.GRAPH_METRIC, {}).get("color", (0,0,0)) if metric in self.plot_metrics else self.COLOR_TEXT
            draw.rectangle([panel_x, y0, panel_x+panel_width, y1], fill=color, outline=(200,200,200))
            draw.text((panel_x+5, y0+6), 
                metric, 
                fill=text_color,
                font=self.ui_fonts["text"])
            self.metric_btn_config.append({
                "x0": panel_x, "y0": y0, "x1": panel_x+panel_width, "y1": y1,
                "metric": metric
            })
            panel_y += btn_h + 4

        # ---- Add "All Duty" and "All RPM" buttons ----
        fan_metrics_present = any(
            m for m in self.selected_metrics if "_Duty" in m or "_RPM" in m
        )

        # ---- Add "All Duty" and "All RPM" buttons only on Fans page ----
        if fan_metrics_present:
            all_btn_h = 30
            all_btn_y = panel_y + 8

            all_btns = [
                {"text": "All Duty", "type": "_Duty"},
                {"text": "All RPM", "type": "_RPM"}
            ]

            for i, btn in enumerate(all_btns):
                y0 = all_btn_y + i*(all_btn_h + 4)
                y1 = y0 + all_btn_h
                draw.rectangle([panel_x, y0, panel_x+panel_width, y1], fill=(100,100,100), outline=(200,200,200))
                draw.text((panel_x+5, y0+6), 
                    btn["text"], 
                    font=self.ui_fonts["text"],
                    fill=(255,255,255))

                def make_callback(suffix):
                    return lambda s=suffix: self.toggle_all_fan_metrics(s)

                self.metric_btn_config.append({
                    "x0": panel_x, "y0": y0, "x1": panel_x+panel_width, "y1": y1,
                    "callback": make_callback(btn["type"]),
                    "text": btn["text"]
                })


        # ---- Graph area ----
        graph_x = panel_width + 2*padding
        graph_y = header_height + padding
        graph_w = self.SCREEN_WIDTH - graph_x - padding
        graph_h = self.SCREEN_HEIGHT - graph_y - footer_height - padding
        x_label = "Value"
        y_label = "Value"


        if self.plot_metrics:
            fig, ax = plt.subplots(figsize=(graph_w/100, graph_h/100), dpi=100, facecolor=(0,0,0))
            ax.set_facecolor((0,0,0))

            y_min, y_max = float('inf'), float('-inf')

            # Get timestamps once
            timestamps = self.history.get_history('timestamp') or []

            for m in self.plot_metrics:
                if m in BenchlabGraph.NON_PLOT_METRICS:
                    continue

                values = self.history.get_history(m) or []
                if not values:
                    continue

                # Align timestamps and values
                min_len = min(len(timestamps), len(values))
                ts_aligned = timestamps[-min_len:]
                vals_aligned = values[-min_len:]

                # Filter numeric data only
                clean = [(t, v) for t, v in zip(ts_aligned, vals_aligned) if isinstance(v, (int, float))]
                if not clean:
                    continue

                clean_ts, clean_vals = zip(*clean)
                x_vals = [datetime.fromtimestamp(t) for t in clean_ts]

                # Plot
                short_label = m.replace("_Power", "").replace("_Current", "").replace("_Voltage", "")
                ax.plot(x_vals, clean_vals, label=short_label)

                y_min = min(y_min, min(clean_vals))
                y_max = max(y_max, max(clean_vals))

            # Configure x-axis as time
            if timestamps:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
                ax.xaxis.set_major_locator(mdates.AutoDateLocator())

            # Y-axis limits with padding
            if y_min == float('inf') or y_max == float('-inf'):
                y_bottom, y_top = 0, 1
            elif y_min == y_max:
                y_bottom = 0 if y_min >= 0 else y_min * 0.9
                y_top = y_max + 0.1 * abs(y_max) if y_max != 0 else 1
            else:
                y_bottom = 0 if y_min >= 0 else y_min * 1.1
                y_top = y_max * 1.1 if y_max != 0 else 1

            ax.set_ylim(y_bottom, y_top)

            ax.tick_params(colors='white', labelcolor='white')
            plt.xticks(rotation=45, ha='right')
            ax.grid(True, color='gray', linestyle='--', linewidth=0.5)

            # Labels
            ax.set_xlabel("Time", color='white')
            units = list({self.wigi.sensor_units.get(m) for m in self.plot_metrics if self.wigi.sensor_units.get(m)})
            ax.set_ylabel(units[0] if len(units)==1 else ", ".join(units) if units else "Value", color='white')

            # Legend
            legend = ax.legend(
                loc='lower center',
                bbox_to_anchor=(0.5, 1.1),
                ncol=5,
                fontsize=10,
                facecolor='black',
                edgecolor='black'
            )
            for text in legend.get_texts():
                text.set_color("white")

            # Convert figure to PIL image
            buf = io.BytesIO()
            fig.tight_layout()
            fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
            plt.close(fig)
            buf.seek(0)
            graph_img = Image.open(buf).convert("RGB")
            graph_img = graph_img.resize((graph_w, graph_h))
            img.paste(graph_img, (graph_x, graph_y))


        # ---- Display ----
        display_image(self.wigidash, img)
