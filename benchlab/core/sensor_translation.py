# benchlab/core/sensor_translation.py

from .structures import SENSOR_VIN_NUM
from .utils import format_temp

def translate_sensor_struct(sensor_struct):
    """Return a flat dict of interpreted sensor values suitable for CSV, graphs, MQTT, etc."""
    data = {}

    # Power
    power = sensor_struct.PowerReadings
    power_cpu = (power[0].Power + power[1].Power) / 1000
    power_gpu = sum([power[i].Power for i in range(6, 11)]) / 1000
    power_mb = sum([power[i].Power for i in range(2, 6)]) / 1000
    power_system = power_cpu + power_gpu + power_mb
    data.update({
        "SYS_Power": power_system,
        "CPU_Power": power_cpu,
        "GPU_Power": power_gpu,
        "MB_Power": power_mb,
    })

    # EPS, ATX, PCIe
    eps_labels = ["EPS1", "EPS2"]
    atx_labels = ["12V", "5V", "5VSB", "3.3V"]
    pcie_labels = ["PCIE8_1", "PCIE8_2", "PCIE8_3", "HPWR1", "HPWR2"]

    for i, label in enumerate(eps_labels):
        data[f"{label}_Voltage"] = power[i].Voltage / 1000
        data[f"{label}_Current"] = power[i].Current / 1000
        data[f"{label}_Power"] = power[i].Power / 1000

    for i, label in enumerate(atx_labels):
        idx = [5, 3, 4, 2][i]
        data[f"{label}_Voltage"] = power[idx].Voltage / 1000
        data[f"{label}_Current"] = power[idx].Current / 1000
        data[f"{label}_Power"] = power[idx].Power / 1000

    for i, label in enumerate(pcie_labels):
        idx = 6 + i
        data[f"{label}_Voltage"] = power[idx].Voltage / 1000
        data[f"{label}_Current"] = power[idx].Current / 1000
        data[f"{label}_Power"] = power[idx].Power / 1000

    # Voltage
    vin_names = [f"VIN_{i}" for i in range(SENSOR_VIN_NUM)]
    for name, value in zip(vin_names, sensor_struct.Vin):
        data[name] = value / 1000
    data["Vdd"] = sensor_struct.Vdd / 1000
    data["Vref"] = sensor_struct.Vref / 1000

    # Temperature
    data["Chip_Temp"] = format_temp(sensor_struct.Tchip)
    data["Ambient_Temp"] = format_temp(sensor_struct.Tamb)
    data["Humidity"] = sensor_struct.Hum / 10
    for i, t in enumerate(sensor_struct.Ts):
        data[f"Temp_Sensor_{i+1}"] = format_temp(t)

    # Fans
    for i, f in enumerate(sensor_struct.Fans):
        data[f"Fan{i+1}_Duty"] = f.Duty
        data[f"Fan{i+1}_RPM"] = f.Tach
        data[f"Fan{i+1}_Status"] = f.Enable
    data["FanExtDuty"] = sensor_struct.FanExtDuty

    return data
