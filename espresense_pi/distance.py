"""RSSI -> distance conversion, matching ESPresense's log-distance path
loss model (see https://espresense.com/guides/calibration/).
"""
from __future__ import annotations


def rssi_to_distance(rssi: int, ref_rssi: int, absorption: float) -> float:
    """distance = 10 ^ ((ref_rssi - rssi) / (10 * absorption))"""
    if rssi == 0:
        return -1.0
    ratio = (ref_rssi - rssi) / (10.0 * absorption)
    return round(10 ** ratio, 2)
