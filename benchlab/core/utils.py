"""
Utility helpers for BENCHLAB
"""

def format_chip_temp(value):
    return round(value / 1, 1)

def format_temp(value):
    temp_c = value / 10
    if temp_c > 1000 or temp_c < -1000:
        return None
    return round(temp_c, 1)