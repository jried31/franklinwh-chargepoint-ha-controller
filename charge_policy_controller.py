"""
Align ChargePoint EV charging state with FranklinWH solar and battery conditions.

This AppDaemon app runs every 5 minutes (configurable) and applies the
following decision logic:

  1. Actively charging:
       a. Peak hours (3:00pm–11:59pm, PG&E EV2-A rate window):
          stop if charger draw ≥ solar output OR total home load ≥ solar output.
       b. Anytime: stop if solar < 0.5 kW and battery is discharging.
          This prevents the EV from draining the home battery at night or on
          heavily overcast days.

  2. Plugged in, not charging:
          start a session within the configured charge window (default 06:30–13:00)
          and battery is not discharging.

All entity IDs and timing parameters live in apps.yaml. No code changes are
needed to adapt this app to a different charger or energy system.

Installation
------------
Place charge_policy_controller.py and models.py in your AppDaemon apps directory
(e.g. /config/appdaemon/apps/) alongside apps.yaml. AppDaemon will hot-reload
the app whenever either file changes.

Dependencies
------------
This app uses only the AppDaemon built-in Home Assistant API (hassapi.Hass).
No additional Python packages are required.
"""

import traceback
import hassapi as hass
from datetime import datetime, time

from models import ChargerDeviceState, ChargerPlugState


# PG&E EV2-A peak window: 3:00pm–11:59pm.
_PEAK_HOURS_START: time = time(15, 0)
_PEAK_HOURS_END: time = time(23, 59)

# Thresholds preventing "drawing from battery" to charge vehicle.
_LOW_SOLAR_KW: float = 0.5   # solar output below this is treated as off
_MIN_DISCHARGE_KW: float = 0.1  # battery output above this is treated as discharging


class ChargePolicyController(hass.Hass):
    """
    AppDaemon app that starts and stops a ChargePoint home charger based on
    live FranklinWH solar production and battery state reported by Home Assistant.

    Configuration is read from self.args (populated from apps.yaml):

    Required keys
    -------------
    charger_device_state  : entity_id — overall charger state sensor
    charger_plug_state    : entity_id — cable plug state sensor
    charger_power_output  : entity_id — instantaneous charger draw (kW)
    charger_turn_on       : entity_id — button to start a charging session
    charger_turn_off      : entity_id — button to stop a charging session
    solar_power_output    : entity_id — FranklinWH instantaneous solar (kW)
    solar_battery_output  : entity_id — FranklinWH instantaneous battery (kW)
    home_load             : entity_id — FranklinWH instantaneous home draw (kW)

    Optional keys (with defaults)
    -----------------------------
    check_interval_seconds  : int — polling interval (default: 300)
    charge_window_start     : str — earliest session start, HH:MM:SS (default: "06:30:00")
    charge_window_end       : str — latest session end, HH:MM:SS (default: "13:00:00")
    """

    def initialize(self) -> None:
        """Register the recurring charge-management callback."""
        interval = int(self.args.get("check_interval_seconds", 300))

        # Setup script run interval
        self.run_every(self.manage_charging, "now", interval)
        self.log(f"ChargePolicyController initialized — evaluating every {interval} s")


    # ------------------------------------------------------------------
    # Program start function
    # -----------------------------------------------------------------
    def manage_charging(self, kwargs: dict) -> None:
        """Scheduled callback: evaluate conditions and act once."""
        try:
            self._evaluate()
        except Exception as e:
            self.log(
                f"{e}\n{traceback.format_exc()}",
                level="ERROR",
            )

    # ------------------------------------------------------------------
    # Core decision logic
    # ------------------------------------------------------------------

    def _evaluate(self) -> None:
        charger_state = self._state(self.args["charger_device_state"])
        self.log(f"Charger device state: {charger_state}")

        plug_state = self._state(self.args["charger_plug_state"])
        solar_kw = self._float(self.args["solar_power_output"])
        battery_kw = self._float(self.args["solar_battery_output"])


        # 2. Currently charging — decide whether to stop.
        if charger_state == ChargerDeviceState.IN_USE.value:
            charger_kw = self._float(self.args["charger_power_output"])
            self.log(
                f"Charging in progress — charger={charger_kw:.3f} kW, "
                f"solar={solar_kw:.3f} kW"
            )

            home_kw = self._float(self.args["home_load"])
            total_kw = home_kw + charger_kw
            self.log(f"Total power (home + charger)={total_kw:.3f} kW")

            if charger_kw > solar_kw or total_kw > solar_kw:
                if self._is_peak_hours():
                    self.log("Insufficient solar during peak hours — stopping session.")
                    self._press(self.args["charger_turn_off"])
                    return

                elif datetime.now().time() < self._charge_window_start():
                    self.log("Not in charging window start time — stopping session.")
                    self._press(self.args["charger_turn_off"])
                    return

                else:
                    self.log("Within charging window — charging permitted.")


        # 3. If idle, start a charging session if within the charge window.
        if (
            charger_state == ChargerDeviceState.NOT_CHARGING.value
            and plug_state == ChargerPlugState.PLUGGED_IN.value
            # and not self._drawing_from_battery(solar_kw, battery_kw)
            and self._charge_window_start() <= datetime.now().time() < self._charge_window_end()
        ):
            self.log("Within charge window with healthy solar — starting session.")
            self._press(self.args["charger_turn_on"])
            return

        self.log("No action required.")


    # ------------------------------------------------------------------
    # Decision helpers
    # ------------------------------------------------------------------

    def _is_peak_hours(self) -> bool:
        return _PEAK_HOURS_START <= datetime.now().time() <= _PEAK_HOURS_END


    def _drawing_from_battery(self, solar_kw: float, battery_kw: float) -> bool:
        """True when solar is negligible and the battery is actively discharging."""
        return solar_kw < _LOW_SOLAR_KW and battery_kw > _MIN_DISCHARGE_KW


    def _charge_window_start(self) -> time:
        raw = self.args.get("charge_window_start", "05:30:00")
        return datetime.strptime(raw, "%H:%M:%S").time()


    def _charge_window_end(self) -> time:
        raw = self.args.get("charge_window_end", "14:30:00")
        return datetime.strptime(raw, "%H:%M:%S").time()

    # ------------------------------------------------------------------
    # Home Assistant helpers
    # ------------------------------------------------------------------

    def _state(self, entity_id: str) -> str:
        value = self.get_state(entity_id)
        if value is None:
            raise ValueError(f"Entity not found in Home Assistant: {entity_id!r}")
        return str(value)


    def _float(self, entity_id: str) -> float:
        raw = self._state(entity_id)
        if raw in ("unknown", "unavailable", "None", ""):
            raise ValueError(
                f"Entity {entity_id!r} returned a non-numeric state: {raw!r}"
            )
        return float(raw)


    def _press(self, entity_id: str) -> None:
        self.call_service("button/press", entity_id=entity_id)
        self.log(f"Button pressed: {entity_id}")
