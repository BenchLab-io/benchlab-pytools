# vu_logo_gen.py

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import hashlib

def generate_sensor_logo(template_path, sensor_name, min_val, max_val, benchlab_port=None):
    """
    Generate a VU sensor logo from a template, caching based on parameters.

    Args:
        template_path (Path or str): Path to the base image template.
        sensor_name (str): Name of the sensor.
        min_val (float): Minimum value.
        max_val (float): Maximum value.
        benchlab_port (str, optional): Benchlab port to display.
    
    Returns:
        Path: Path to the saved generated logo.
    """
    template_path = Path(template_path)
    if not template_path.exists():
        raise FileNotFoundError(f"Template not found: {template_path}")

    # --- Create a unique filename based on parameters ---
    params_str = f"{sensor_name}_{min_val}_{max_val}_{benchlab_port}"
    # Use a hash to avoid extremely long filenames
    hash_digest = hashlib.md5(params_str.encode("utf-8")).hexdigest()
    safe_name = sensor_name.replace(" ", "_").replace("/", "-")
    
    output_dir = Path(__file__).parent / "generated"
    output_dir.mkdir(exist_ok=True)
    output_path = output_dir / f"{safe_name}_{hash_digest}_logo.png"

    # If file already exists, return cached version
    if output_path.exists():
        return output_path

    # --- Load template and draw ---
    img = Image.open(template_path).convert("L")
    draw = ImageDraw.Draw(img)
    width, height = img.size
    header_height = 25

    # Fonts
    font_path = Path(__file__).parent / "assets/Barlow-Bold.ttf"
    font_sensor = ImageFont.truetype(str(font_path), 20)
    font_minmax = ImageFont.truetype(str(font_path), 12)

    # Sensor name (top-center)
    bbox = draw.textbbox((0, 0), sensor_name, font=font_sensor)
    x = (width - (bbox[2] - bbox[0])) // 2
    y = header_height + 10
    draw.text((x, y), sensor_name, fill=0, font=font_sensor)

    # Benchlab port (bottom-center)
    if benchlab_port:
        text_port = str(benchlab_port)
        bbox_port = draw.textbbox((0, 0), text_port, font=font_minmax)
        x_port = (width - (bbox_port[2] - bbox_port[0])) // 2
        y_port = height - (bbox_port[3] - bbox_port[1]) - 20
        draw.text((x_port, y_port), text_port, fill=0, font=font_minmax)

    # Min (bottom-left)
    text_min = f"Min: {min_val}"
    bbox_min = draw.textbbox((0, 0), text_min, font=font_minmax)
    y_min = height - (bbox_min[3] - bbox_min[1]) - 6
    draw.text((2, y_min), text_min, fill=0, font=font_minmax)

    # Max (bottom-right)
    text_max = f"Max: {max_val}"
    bbox_max = draw.textbbox((0, 0), text_max, font=font_minmax)
    x_max = width - (bbox_max[2] - bbox_max[0]) - 2
    y_max = height - (bbox_max[3] - bbox_max[1]) - 6
    draw.text((x_max, y_max), text_max, fill=0, font=font_minmax)

    # Save as 1-bit PNG
    img.convert("1", dither=Image.NONE).save(output_path, format="PNG")
    return output_path

# --- Quick test ---
if __name__ == "__main__":
    template = Path(__file__).parent / "assets/bl_dial_144x200.png"
    output = generate_sensor_logo(template, "Temp Sensor", 0, 100, benchlab_port="35001")
    print(f"Generated logo at: {output}")
