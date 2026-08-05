"""Bluetooth fallback client for the Delta Pros, wrapping vendored eflib.

Speaks the dashboard's existing dialect: snapshots come back as a
cloud-quota-shaped dict (same field names and scalings as quota/all, so
`normalize()` and the controls readback logic work unchanged), and writes
take a `controls.py` key via set_control().

Sync facade over a private asyncio loop thread — Flask is sync. All radio
work happens on that loop; callers block with a timeout.

Requires ECOFLOW_USER_ID (fetch once with scripts/ble_login.py) and the
units bound to that account. Connections persist once made; a call against
an out-of-range unit raises BleError quickly rather than hanging.
"""

import asyncio
import threading
import time

from bleak import BleakScanner

from .ble.eflib import NewDevice, sn_from_advertisement

SCAN_S = 12          # advertisement scan window
CONNECT_S = 25       # connect + auth budget
CALL_S = 45          # overall per-call budget from the sync side
FIRST_DATA_S = 8     # wait for first heartbeats after connect


class BleError(Exception):
    pass


class BleUnsupported(BleError):
    """This control has no known BLE packet on the original Delta Pro."""


# controls.py key -> (device coroutine name, value adapter)
_CONTROL_MAP = {
    "ac_out": ("enable_ac_ports", bool),
    "xboost": ("enable_xboost", bool),
    "car_out": ("enable_dc_12v_port", bool),
    "max_chg_soc": ("set_battery_charge_limit_max", int),
    "min_dsg_soc": ("set_battery_charge_limit_min", int),
    "ac_chg_watts": ("set_ac_charging_speed", int),
    # cloud sends quiet (beepState 1 = silent); BLE takes buzzer-enabled
    "quiet": ("set_buzzer", lambda v: not v),
    "lcd_brightness": ("set_screen_brightness", int),
    "lcd_timeout": ("set_screen_timeout", int),
    "unit_standby": ("set_system_standby_time", int),
    "ac_standby": ("set_ac_standby_time", int),
    "car_in_amps": ("set_car_charging_current", lambda v: int(v) // 1000),
    # pv_chg_type / bypass_auto / gen_auto_*: eflib has candidate packets
    # (set_dc_charging_type, enable_ac_always_on) but their semantics are
    # not confirmed to match the cloud commands — refuse rather than guess.
}


def snapshot_to_quota(dev):
    """Map eflib Device fields onto cloud quota/all names and scalings.

    Partial by design: BLE heartbeats don't carry everything quota/all does
    (no pack mAh capacities, no lifetime Wh counters). Missing keys are
    simply absent — normalize() treats them as None.
    """
    def g(name):
        return getattr(dev, name, None)

    def put(q, key, value, scale=1):
        if value is not None:
            q[key] = value * scale if scale != 1 else value

    q = {}
    put(q, "ems.f32LcdShowSoc", g("battery_level"))
    put(q, "ems.minDsgSoc", g("battery_charge_limit_min"))
    put(q, "ems.maxChargeSoc", g("battery_charge_limit_max"))
    put(q, "inv.outputWatts", g("ac_output_power"))
    put(q, "inv.inputWatts", g("ac_input_power"))
    put(q, "inv.acInVol", g("ac_input_voltage"), 1000)
    ac = g("ac_ports")
    put(q, "inv.cfgAcEnabled", None if ac is None else int(ac))
    put(q, "inv.cfgSlowChgWatts", g("ac_charging_speed"))
    # eflib pre-divides these to human units; cloud serves the raw 0.1/0.01 forms
    put(q, "mppt.inWatts", g("dc_input_power"), 10)
    put(q, "mppt.inVol", g("dc_input_voltage"), 10)
    put(q, "mppt.inAmp", g("dc_input_current"), 100)
    put(q, "mppt.carOutWatts", g("dc_output_power"), 10)
    car = g("dc_12v_port")
    put(q, "mppt.carState", None if car is None else int(car))
    put(q, "pd.wattsInSum", g("input_power"))
    put(q, "pd.wattsOutSum", g("output_power"))
    for cloud, ble in [("pd.usb1Watts", "usba_output_power"),
                       ("pd.usb2Watts", "usba2_output_power"),
                       ("pd.qcUsb1Watts", "qc_usb1_output_power"),
                       ("pd.qcUsb2Watts", "qc_usb2_output_power"),
                       ("pd.typec1Watts", "usbc_output_power"),
                       ("pd.typec2Watts", "usbc2_output_power")]:
        put(q, cloud, g(ble))
    # master pack is implicitly present; extra packs from kit info
    q["ems.bms0Online"] = 3
    b1 = g("battery_1_enabled")
    q["ems.bms1Online"] = 3 if b1 else 0
    if b1:
        put(q, "bmsSlave1.f32ShowSoc", g("battery_1_battery_level"))
    b2 = g("battery_2_enabled")
    q["ems.bms2Online"] = 3 if b2 else 0
    q["_ble"] = 1     # marks the snapshot's origin for debugging
    return q


class BleClient:
    """One client managing both units over a shared background loop."""

    def __init__(self, user_id, sn_map):
        if not user_id:
            raise BleError("ECOFLOW_USER_ID is not set — run scripts/ble_login.py")
        self._user_id = user_id
        self._sns = dict(sn_map)          # unit letter -> SN
        self._devices = {}                # SN -> eflib Device
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever,
                         name="ble-loop", daemon=True).start()

    # ---- sync facade ----

    def get_quota_all(self, sn):
        return self._call(self._snapshot(sn))

    def set_control(self, sn, key, value):
        if key not in _CONTROL_MAP:
            raise BleUnsupported(f"{key} has no verified BLE command")
        return self._call(self._set(sn, key, value))

    def _call(self, coro):
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return fut.result(CALL_S)
        except (BleError, BleUnsupported):
            raise
        except TimeoutError:
            fut.cancel()
            raise BleError("BLE call timed out (unit out of range?)")
        except Exception as e:
            raise BleError(f"BLE failure: {e}") from e

    # ---- loop side ----

    async def _device(self, sn):
        dev = self._devices.get(sn)
        if dev is not None and dev.is_connected:
            return dev
        found = {}

        def cb(ble_dev, adv):
            raw = sn_from_advertisement(adv)
            if raw is not None and raw.decode("ascii", "ignore") == sn:
                found[sn] = (ble_dev, adv)

        scanner = BleakScanner(cb)
        await scanner.start()
        deadline = time.monotonic() + SCAN_S
        while sn not in found and time.monotonic() < deadline:
            await asyncio.sleep(0.25)
        await scanner.stop()
        if sn not in found:
            raise BleError(f"{sn} not advertising within range")
        dev = NewDevice(*found[sn])
        if dev is None or not hasattr(dev, "enable_ac_ports"):
            raise BleError(f"{sn} found but not recognized as a Delta Pro")
        await dev.connect(user_id=self._user_id)
        await dev.wait_until_authenticated_or_error(timeout=CONNECT_S)
        self._devices[sn] = dev
        return dev

    async def _snapshot(self, sn):
        dev = await self._device(sn)
        # heartbeats rotate every ~0.35s; give the first full cycle a moment
        deadline = time.monotonic() + FIRST_DATA_S
        while getattr(dev, "battery_level", None) is None \
                and time.monotonic() < deadline:
            await asyncio.sleep(0.4)
        if getattr(dev, "battery_level", None) is None:
            raise BleError(f"{sn} connected but sent no data")
        return snapshot_to_quota(dev)

    async def _set(self, sn, key, value):
        dev = await self._device(sn)
        method, adapt = _CONTROL_MAP[key]
        await getattr(dev, method)(adapt(value))
        return True
