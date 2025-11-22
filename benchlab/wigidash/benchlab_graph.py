# benchlab_graph.py

from PIL import Image, ImageDraw, ImageFont
from datetime import datetime
import io
import matplotlib
matplotlib.use("Agg") 
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import os
import signal
import time

from benchlab.wigidash.benchlab_utils import display_image, get_logger


logger = get_logger("BenchlabGraph")


class BenchlabGraph:
    SCREEN_WIDTH = 1016
    SCREEN_HEIGHT = 592
    HEADER_HEIGHT = 60
    FOOTER_HEIGHT = 60

    COLOR_TITLE = (255, 255, 255)
    COLOR_LABEL = (200, 200, 200)
    COLOR_VALUE = (238,238,238)
    COLOR_SECTION = (252, 228, 119)
    COLOR_FOOTER = (128, 128, 128)
    PADDING = 8

    NON_PLOT_METRICS = {
        "Fan1_Status", "Fan2_Status", "Fan3_Status", "Fan4_Status",
        "Fan5_Status", "Fan6_Status", "Fan7_Status", "Fan8_Status",
        "Fan9_Status", "FanExtDuty", "Fans"
    }

    def __init__(self, wigidash, wigi, metrics):
        self.wigidash = wigidash
        self.wigi = wigi
        self.last_touch_time = 0
        self.all_metrics = list(wigi.sensor_data.keys())
        self.selected_metrics = metrics.copy()
        self.running = False
        self.font_header = None
        self.font_text = None
        self.footer_btn_config = []
        self.metric_btn_config = []

        # Assets
        base_dir = os.path.dirname(os.path.abspath(__file__))

        font_button_path = os.path.join(base_dir, "assets", "Inter.ttf")
        font_header_path = os.path.join(base_dir, "assets", "Inter-Bold.ttf")
        font_text_path = os.path.join(base_dir, "assets", "Inter.ttf")
        font_title_path = os.path.join(base_dir, "assets", "Inter.ttf")
        logo_path = os.path.join(base_dir, "assets", "benchlab.png")

        try:
            self.font_button = ImageFont.truetype(font_button_path, 30)
            self.font_header = ImageFont.truetype(font_header_path, 30)
            self.font_text = ImageFont.truetype(font_text_path, 14)
            self.font_title = ImageFont.truetype(font_title_path, 18)
        except Exception:
            self.font_button = self.font_header = self.font_title = self.font_text = ImageFont.load_default()
            logger.warning("Default font loaded due to missing assets.")

        try:
            logo_img = Image.open(logo_path).convert("RGBA")
            logo_img.thumbnail((60, 60), Image.Resampling.LANCZOS)
            self.logo = logo_img
        except Exception as e:
            logger.warning("Logo load error: %s", e)
            self.logo = None

    # -------------------------------
    # Page Lifecycle
    # -------------------------------

    def start(self):
        self.running = True
        logger.info(f"Graph page started with metrics: {self.selected_metrics}")

    def stop(self):
        self.running = False
        logger.info("Graph page stopped")

    def return_to_overview(self):
        logger.info("Returning to Overview page")
        self.running = False

    # -------------------------------
    # Touch Handling
    # -------------------------------

    def check_touch(self, touch):
        if not getattr(self, "running", False):
            return

        now = time.time()
        if now - getattr(self, "last_touch_time", 0) < 0.5:
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

        padding = 8
        header_height = 60
        footer_height = 40
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
            draw.text((x+8, y+8), title, fill=self.COLOR_SECTION, font=self.font_title)

        # ---- Initialize plot_metrics if missing ----
        if not hasattr(self, "plot_metrics"):
            self.plot_metrics = self.selected_metrics.copy()

        # ---- Header ----
        draw.rectangle([0, 0, self.SCREEN_WIDTH, header_height], fill=self.COLOR_SECTION)

        if self.logo:
            logo_y = (self.HEADER_HEIGHT - self.logo.height)//2
            img.paste(self.logo, (10, logo_y), self.logo)
            img.paste(self.logo, (self.SCREEN_WIDTH - self.logo.width - 10, logo_y), self.logo)

        header_text = "BENCHLAB TELEMETRY HISTORY"
        bbox = draw.textbbox((0, 0), header_text, font=self.font_header)
        title_x = (self.SCREEN_WIDTH - (bbox[2]-bbox[0])) // 2
        title_y = (header_height - (bbox[3]-bbox[1])) // 2
        draw.text((title_x, title_y), header_text, fill=(0,0,0), font=self.font_header)

        # ---- Footer ----
        footer_height = 40
        y_footer = self.SCREEN_HEIGHT - footer_height
        btn_width, btn_height = 120, 40
        btn_spacing = padding  # space between buttons

        # Info box takes all space minus buttons area
        footer_rect = [padding, y_footer, self.SCREEN_WIDTH - (padding + (btn_width + btn_spacing) * 2), self.SCREEN_HEIGHT - padding]
        draw.rectangle(footer_rect, outline=(200, 200, 200), fill=(20, 20, 20))

        # --- Footer info text ---
        info_parts = [f"Port: {self.wigi.ser.port if self.wigi.ser else 'N/A'}"]
        if self.wigi.device_info:
            info_parts.append(f"Vendor ID: 0x{self.wigi.device_info.get('VendorId',0):03X}")
            info_parts.append(f"Product ID: 0x{self.wigi.device_info.get('ProductId',0):03X}")
            info_parts.append(f"FW Version: 0x{self.wigi.device_info.get('FwVersion',0):02X}")
        info_parts.append(f"UID: {self.wigi.uid or 'N/A'}")
        info_line = " | ".join(info_parts)

        # Center text vertically inside footer box
        text_bbox = self.font_text.getbbox(info_line)
        text_height = text_bbox[3] - text_bbox[1]
        text_y = y_footer + (footer_height - text_height) // 2
        draw.text((padding + 8, text_y), info_line, fill=self.COLOR_VALUE, font=self.font_text)

        # ---- Footer Buttons ----
        btns = [
            {"text": "Shutdown", "callback": lambda: signal.raise_signal(signal.SIGINT), "color": (255, 99, 71)},
            {"text": "Overview", "callback": self.return_to_overview, "color": (252, 228, 119)},
        ]

        self.footer_btn_config = []

        # Start button placement from the right
        btn_x = self.SCREEN_WIDTH - padding - btn_width
        for btn in btns:
            btn_y = y_footer + (footer_height - btn_height) // 2
            btn_x1, btn_y1 = btn_x + btn_width, btn_y + btn_height

            # Draw button
            draw.rectangle([btn_x, btn_y, btn_x1, btn_y1], fill=btn["color"], outline=(200, 200, 200))

            # Center text inside button
            text_bbox = self.font_text.getbbox(btn["text"])
            text_height = text_bbox[3] - text_bbox[1]
            text_width = text_bbox[2] - text_bbox[0]
            text_x = btn_x + (btn_width - text_width) // 2
            text_y = btn_y + (btn_height - text_height) // 2
            draw.text((text_x, text_y), btn["text"], fill=(0, 0, 0), font=self.font_text)

            # Save button config for touch events
            self.footer_btn_config.append({
                "x0": btn_x,
                "y0": btn_y,
                "x1": btn_x1,
                "y1": btn_y1,
                "callback": btn["callback"],
                "text": btn["text"]
            })

            # Move left for next button
            btn_x -= (btn_width + btn_spacing)

        # ---- Left panel: metrics ----
        panel_width = 200
        btn_h = 25
        panel_x = padding
        panel_y = header_height + padding
        self.metric_btn_config = []

        draw.text((panel_x+5, panel_y), "Metrics", font=self.font_title, fill=self.COLOR_SECTION)
        panel_y += self.font_title.getbbox("Metrics")[3] + 8

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
            text_color = (0,0,0) if duty_m in self.plot_metrics else (252,228,119)
            draw.rectangle([panel_x, y0, panel_x+half_width, y1], fill=color, outline=(200,200,200))
            draw.text((panel_x+5, y0+6), duty_m, font=self.font_text, fill=text_color)
            self.metric_btn_config.append({
                "x0": panel_x, "y0": y0, "x1": panel_x+half_width, "y1": y1,
                "metric": duty_m
            })

            # Right half: RPM
            x_start = panel_x + half_width + spacing
            color = (252, 228, 119) if rpm_m in self.plot_metrics else (50,50,50)
            text_color = (0,0,0) if rpm_m in self.plot_metrics else (252,228,119)
            draw.rectangle([x_start, y0, panel_x+panel_width, y1], fill=color, outline=(200,200,200))
            draw.text((x_start+5, y0+6), rpm_m, font=self.font_text, fill=text_color)
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
            text_color = (0,0,0) if metric in self.plot_metrics else (252,228,119)
            draw.rectangle([panel_x, y0, panel_x+panel_width, y1], fill=color, outline=(200,200,200))
            draw.text((panel_x+5, y0+6), metric, font=self.font_text, fill=text_color)
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
                draw.text((panel_x+5, y0+6), btn["text"], font=self.font_text, fill=(255,255,255))

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

            # Get all timestamps once
            timestamps = self.wigi.history.get_history('timestamp')

            for m in self.plot_metrics:
                if m in BenchlabGraph.NON_PLOT_METRICS:
                    continue

                history = self.wigi.history.get_history(m)
                if not history:
                    continue

                clean = [
                    (t, v) for t, v in zip(timestamps[-len(history):], history)
                    if isinstance(v, (int, float))
                ]
                if not clean:
                    continue

                clean_ts, clean_vals = zip(*clean)

                if timestamps and len(timestamps) >= len(history):
                    ts_plot = timestamps[-len(history):]  # last N timestamps
                    x_vals = [datetime.fromtimestamp(t) for t in ts_plot]
                    x_label = "Time"
                else:
                    x_vals = range(len(clean_vals))
                    x_label = "Sample #"

                short_label = (
                    m.replace("_Power", "")
                     .replace("_Current", "")
                     .replace("_Voltage", "")
                )

                ax.plot(x_vals, clean_vals, label=short_label)

                y_min = min(y_min, min(clean_vals))
                y_max = max(y_max, max(clean_vals))

            if timestamps:
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M:%S'))
                ax.xaxis.set_major_locator(mdates.AutoDateLocator())

            # Only set y-limits if we got numeric data
            if y_min == float('inf') or y_max == float('-inf'):
                y_bottom, y_top = 0, 1
            elif y_min == y_max:
                y_bottom = y_min - 0.1*abs(y_min) if y_min < 0 else 0
                y_top = y_max + 0.1*abs(y_max) if y_max != 0 else 1
            else:
                # 10% padding on both ends
                y_bottom = 0 if y_min >= 0 else y_min * 1.1
                y_top = y_max * 1.1 if y_max != 0 else 1

            ax.set_ylim(y_bottom, y_top)

            ax.tick_params(colors='white', labelcolor='white')
            plt.xticks(rotation=45, ha='right')
            ax.grid(True, color='gray', linestyle='--', linewidth=0.5)

            # X-axis
            ax.set_xlabel(x_label, color='white')

            # Y-axis (use units if available)
            units = []
            for m in self.plot_metrics:
                u = self.wigi.sensor_units.get(m)
                if u and u not in units:
                    units.append(u)

            if len(units) == 0:
                y_label = "Value"
            elif len(units) == 1:
                y_label = units[0]
            else:
                # Multiple units — list all of them
                y_label = ", ".join(units)

            ax.set_ylabel(y_label, color='white')

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
