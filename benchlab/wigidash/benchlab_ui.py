from PIL import Image, ImageFont
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")


class UITheme:
    SCREEN_WIDTH = 1016
    SCREEN_HEIGHT = 592
    HEADER_HEIGHT = 60
    FOOTER_HEIGHT = 56
    PADDING = 8

    COLOR_SECTION = (252, 228, 119)
    COLOR_BG = (0, 0, 0)
    COLOR_CARD = (15, 15, 15)
    COLOR_TEXT = (238, 238, 238)
    COLOR_MUTED = (128, 128, 128)
    COLOR_BORDER = (200, 200, 200)


class UIButton:
    SELECT_DEVICE = "select_device"
    OVERVIEW = "overview"
    SHUTDOWN = "shutdown"
    GRAPH_METRIC = "graph_metric"

BUTTON_DEFS = {
    UIButton.SELECT_DEVICE: {
        "text": "Select Device",
        "color": (252, 228, 119),
    },
    UIButton.OVERVIEW: {
        "text": "Overview",
        "color": (252, 228, 119),
    },
    UIButton.SHUTDOWN: {
        "text": "Shutdown",
        "color": (255, 99, 71),
    },
    UIButton.GRAPH_METRIC: {
        "color": (0, 0, 0),
    },
}


def bind_button(button_id, callback):
    spec = BUTTON_DEFS[button_id]
    return {
        "id": button_id,
        "text": spec.get("text", ""),
        "color": spec["color"],
        "callback": callback,
    }

def load_fonts():
    try:
        return {
            "header": ImageFont.truetype(os.path.join(ASSETS_DIR, "Inter-Bold.ttf"), 30),
            "title":  ImageFont.truetype(os.path.join(ASSETS_DIR, "Inter.ttf"), 18),
            "text":   ImageFont.truetype(os.path.join(ASSETS_DIR, "Inter.ttf"), 14),
            "button": ImageFont.truetype(os.path.join(ASSETS_DIR, "Inter.ttf"), 14),
        }
    except Exception:
        default = ImageFont.load_default()
        return dict(header=default, title=default, text=default, button=default)


def load_logo(max_size=(60, 60)):
    logo_path = os.path.join(ASSETS_DIR, "benchlab.png")
    try:
        logo_img = Image.open(logo_path).convert("RGBA")
        logo_img.thumbnail(max_size, Image.Resampling.LANCZOS)
        return logo_img
    except Exception as e:
        # Optionally log warning here if you have a logger
        return None


def draw_header(draw, img, fonts, title, logo=None):
    draw.rectangle([0, 0, UITheme.SCREEN_WIDTH, UITheme.HEADER_HEIGHT], fill=UITheme.COLOR_SECTION)

    if logo:
        y = (UITheme.HEADER_HEIGHT - logo.height) // 2
        img.paste(logo, (10, y), mask=logo)
        img.paste(logo, (UITheme.SCREEN_WIDTH - logo.width - 10, y), mask=logo)


    bbox = draw.textbbox((0, 0), title, font=fonts["header"])
    x = (UITheme.SCREEN_WIDTH - (bbox[2] - bbox[0])) // 2
    y = (UITheme.HEADER_HEIGHT - (bbox[3] - bbox[1])) // 2
    draw.text((x, y), title, fill=(0, 0, 0), font=fonts["header"])



def draw_footer(draw, fonts, info_text, buttons):
    hitboxes = []
    pad = UITheme.PADDING

    footer_top = UITheme.SCREEN_HEIGHT - UITheme.FOOTER_HEIGHT
    footer_bottom = UITheme.SCREEN_HEIGHT

    # Inner content area (accounts for padding + border)
    inner_top = footer_top + pad
    inner_bottom = footer_bottom - pad
    inner_height = inner_bottom - inner_top

    # ---- Layout calculations ----
    btn_w = 120
    btn_h = inner_height  # EXACT same height as info box
    total_btn_width = len(buttons) * btn_w + (len(buttons) - 1) * pad

    info_right = UITheme.SCREEN_WIDTH - pad - total_btn_width - pad

    # ---- Info background ----
    draw.rectangle(
        [pad, inner_top, info_right, inner_bottom],
        fill=(20, 20, 20),
        outline=UITheme.COLOR_BORDER
    )

    # ---- Info text (vertically centered in inner area) ----
    tb = fonts["text"].getbbox(info_text)
    text_h = tb[3] - tb[1]
    text_y = inner_top + (inner_height - text_h) // 2

    draw.text(
        (pad + 8, text_y),
        info_text,
        fill=UITheme.COLOR_TEXT,
        font=fonts["text"]
    )

    # ---- Buttons ----
    x = UITheme.SCREEN_WIDTH - pad - btn_w

    for btn in buttons:
        y = inner_top

        draw.rectangle(
            [x, y, x + btn_w, y + btn_h],
            fill=btn["color"],
            outline=UITheme.COLOR_BORDER
        )

        # Center text inside button
        tb = fonts["button"].getbbox(btn["text"])
        tx = x + (btn_w - (tb[2] - tb[0])) // 2
        ty = y + (btn_h - (tb[3] - tb[1])) // 2

        draw.text((tx, ty), btn["text"], fill=(0, 0, 0), font=fonts["button"])

        # Hitbox == button rectangle
        hitboxes.append({
            "x0": x,
            "y0": y,
            "x1": x + btn_w,
            "y1": y + btn_h,
            "callback": btn["callback"],
            "text": btn["text"]
        })

        x -= btn_w + pad

    return hitboxes
