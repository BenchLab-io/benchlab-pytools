# wigidash_widget.py

from ctypes import Structure, c_int16, c_uint16, c_uint8, c_uint32

import logging
import sys

from benchlab.wigidash.benchlab_utils import get_logger

logger = get_logger("WigidashWidget")

class WidgetConfig(Structure):
    _pack_ = 4
    _fields_ = [
        ('X', c_int16),
        ('Y', c_int16),
        ('Width', c_int16),
        ('Height', c_int16),
        ('BaseClr', c_uint16),
        ('DrawAddr', c_uint32),
        ('DrawLock', c_uint8),
        ('InvalidateFlag', c_uint8),
        ('UpdateFromCache', c_uint8)
    ]

    def __str__(self):
        return (f'X: {self.X}, Y: {self.Y}, Width: {self.Width}, Height: {self.Height}, '
                f'BaseClr: {self.BaseClr}, DrawAddr: {self.DrawAddr}, '
                f'DrawLock: {self.DrawLock}, InvalidateFlag: {self.InvalidateFlag}, '
                f'UpdateFromCache: {self.UpdateFromCache}')

    # --- Properties with logging ---
    @property
    def DrawLock(self):
        return self._DrawLock

    @DrawLock.setter
    def DrawLock(self, value):
        logger.debug(f"DrawLock changed: {self._DrawLock} → {value}")
        self._DrawLock = value

    @property
    def InvalidateFlag(self):
        return self._InvalidateFlag

    @InvalidateFlag.setter
    def InvalidateFlag(self, value):
        logger.debug(f"InvalidateFlag changed: {self._InvalidateFlag} → {value}")
        self._InvalidateFlag = value

    @property
    def UpdateFromCache(self):
        return self._UpdateFromCache

    @UpdateFromCache.setter
    def UpdateFromCache(self, value):
        logger.debug(f"UpdateFromCache changed: {self._UpdateFromCache} → {value}")
        self._UpdateFromCache = value

    @classmethod
    def create_fullscreen(cls, width=1016, height=592):
        """Create a fullscreen widget configuration"""
        widget = cls()
        widget.X = 0
        widget.Y = 0
        widget.Width = width
        widget.Height = height
        widget.DrawAddr = 0
        logger.info(f"Created fullscreen widget: {widget}")
        return widget

    @classmethod
    def create_custom(cls, x=0, y=0, width=100, height=100, base_color=0):
        """Create a custom positioned widget"""
        widget = cls()
        widget.X = x
        widget.Y = y
        widget.Width = width
        widget.Height = height
        widget.BaseClr = base_color
        widget.DrawAddr = 0
        logger.info(f"Created custom widget: {widget}")
        return widget