# benchlab_utils.py

from PIL import Image
import logging
import numpy as np
import sys
import time
import threading

# ----------------------------------------
# Logging setup
# ----------------------------------------

# shared logger for all benchlab modules
def get_logger(name="BenchlabUtils", level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Remove any duplicate handlers
    if logger.hasHandlers():
        logger.handlers.clear()

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter(
        '[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    ))
    logger.addHandler(ch)
    logger.propagate = False  # prevent double logging via root logger
    return logger

logger = get_logger("BenchlabUtils")

def setup_logging(module_levels=None, default_level=logging.INFO):
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        '[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%H:%M:%S'
    ))

    root = logging.getLogger()
    root.setLevel(default_level)

    if not root.hasHandlers():
        root.addHandler(handler)

    if module_levels:
        for name, level in module_levels.items():
            lg = logging.getLogger(name)
            lg.setLevel(level)
            if not lg.hasHandlers():
                lg.addHandler(handler)
            lg.propagate = False


# ----------------------------------------
# Display / Image helpers
# ----------------------------------------

def image_to_rgb565(img: Image.Image) -> bytes:
    """Convert PIL image to raw RGB565 bytes using NumPy (faster)."""
    img = img.convert('RGB')
    arr = np.array(img, dtype=np.uint8)  # shape: (H, W, 3)

    r = (arr[..., 0] >> 3).astype(np.uint16)  # 5 bits
    g = (arr[..., 1] >> 2).astype(np.uint16)  # 6 bits
    b = (arr[..., 2] >> 3).astype(np.uint16)  # 5 bits

    rgb565 = (r << 11) | (g << 5) | b  # shape: (H, W)
    return rgb565.flatten().tobytes()

def display_image(wigidash, img: Image.Image, page=0, widget_id=0):
    """Display an image on the WigiDash widget."""
    if not wigidash:
        logging.getLogger(__name__).warning("display_image: no wigidash device")
        return

    rgb565 = image_to_rgb565(img)
    wigidash.write_to_widget(page=page, widget_id=widget_id, offset=0, data=rgb565)


def clear_display(wigidash, width, height):
    """Fill the screen with black."""
    img = Image.new('RGB', (width, height), (0, 0, 0))
    display_image(wigidash, img)
    logging.getLogger(__name__).info("Display cleared.")


# ----------------------------------------
# Screen keepalive
# ----------------------------------------

class KeepAliveManager:
    """Keeps the WigiDash screen from going to sleep."""

    def __init__(self, wigidash, interval=5.0):
        self.wigidash = wigidash
        self.interval = interval
        self.running = False
        self.thread = None

    def start(self):
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=1.0)
            self.thread = None

    def mark_active(self):
	    try:
	        if self.wigidash and hasattr(self.wigidash, "clear_screen_timeout"):
	            self.wigidash.clear_screen_timeout()
	    except Exception as e:
	        logger.warning("Keepalive error: %s", e)

    def loop(self):
        step = 0.1
        while self.running:
            try:
                if self.wigidash:
                    self.wigidash.clear_screen_timeout()
            except Exception as e:
                logger.warning(f"Keepalive error: {e}")

            for _ in range(int(self.interval / step)):
                if not self.running:
                    break
                time.sleep(step)


# ----------------------------------------
# Shutdown function
# ----------------------------------------

def shutdown_wigidash(wigi_instance):
    """
    Safely shut down a WigiDash session.

    wigi_instance: instance of BenchlabWigi or any object
                   with .keepalive_manager, .wigidash, .usb_dev attributes.
    """
    logger.info("Shutting down WigiDash session...")
    
    # Stop keepalive
    if getattr(wigi_instance, "keepalive_manager", None):
        wigi_instance.keepalive_manager.stop()
    
    # Clear / snooze display
    wigidash = getattr(wigi_instance, "wigidash", None)
    if wigidash:
        try:
            wigidash.clear_page(0)
            wigidash.snooze_device()
        except Exception as e:
            logger.warning(f"Error shutting down display: {e}")
    
    # Disconnect USB
    usb_dev = getattr(wigi_instance, "usb_dev", None)
    if usb_dev:
        try:
            logger.info("Disconnecting USB device...")
            usb_dev.disconnect()
            logger.info("USB device disconnected.")
        except Exception as e:
            logger.warning(f"Error disconnecting USB: {e}")
