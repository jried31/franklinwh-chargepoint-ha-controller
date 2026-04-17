"""
Tests for ChargePolicyController.

These tests run entirely in-process. No AppDaemon or Home Assistant instance is needed.

Run with:
    uv run --extra dev pytest

Current decision logic under test
----------------------------------
1. if device is IN_USE, then stop when:
     a. load > solar AND peak hours (15:00–23:59)
     b. load > solar AND time is before charge_window_start
2. If NOT_CHARGING and PLUGGED_IN, then start session if within charge window
   (charge_window_start ≤ now < charge_window_end).
   Battery discharge state is not evaluated for start/stop decisions.
"""

import sys
from datetime import datetime
from unittest.mock import MagicMock
import pytest

# ---------------------------------------------------------------------------
# Stub hassapi before importing the app module.
# ---------------------------------------------------------------------------

class _FakeHass:
    """Minimal stand-in for hassapi.Hass."""
    args: dict = {}
    def get_state(self, *a, **kw): ...
    def call_service(self, *a, **kw): ...
    def log(self, *a, **kw): ...
    def run_every(self, *a, **kw): ...

_hass_stub = MagicMock()
_hass_stub.Hass = _FakeHass
sys.modules.setdefault("hassapi", _hass_stub)

from charge_policy_controller import ChargePolicyController  # noqa: E402

# ---------------------------------------------------------------------------
# Shared test configuration
# ---------------------------------------------------------------------------

_ARGS = {
    "charger_device_state":   "sensor.charger_state",
    "charger_plug_state":     "sensor.charger_plug",
    "charger_power_output":   "sensor.charger_power",
    "charger_turn_on":        "button.charger_on",
    "charger_turn_off":       "button.charger_off",
    "solar_power_output":     "sensor.solar",
    "solar_battery_output":   "sensor.battery",
    "home_load":              "sensor.home_load",
    "charge_window_start":    "06:30:00",
    "charge_window_end":      "13:00:00",
    "check_interval_seconds": "300",
}

# Fixed datetimes used across tests
_WITHIN_WINDOW = datetime(2026, 4, 16, 7, 0)   # 07:00 — off-peak, within charge window
_PEAK          = datetime(2026, 4, 16, 16, 0)  # 16:00 — within PG&E peak window (15:00–23:59)
_BEFORE_WINDOW = datetime(2026, 4, 16, 6, 0)   # 06:00 — before charge window start (06:30)
_AFTER_WINDOW  = datetime(2026, 4, 16, 14, 0)  # 14:00 — after charge window end (13:00)
_AFTER_PEAK    = datetime(2026, 4, 17, 0, 1)   # 00:01 — after peak window end (23:59)


def _make(states: dict) -> tuple[ChargePolicyController, MagicMock]:
    """Construct a controller whose HA state is driven by *states*."""
    ctrl = ChargePolicyController.__new__(ChargePolicyController)
    ctrl.args = _ARGS.copy()
    ctrl.get_state = lambda entity_id, **kw: states.get(entity_id)
    ctrl.log = MagicMock()
    ctrl.run_every = MagicMock()
    svc = MagicMock()
    ctrl.call_service = svc
    return ctrl, svc


@pytest.fixture
def freeze(monkeypatch):
    """Fix datetime.now() to a given datetime for the duration of the test."""
    def _fix(dt: datetime) -> None:
        fake = MagicMock(wraps=datetime)
        fake.now.return_value = dt
        fake.strptime = datetime.strptime
        monkeypatch.setattr("charge_policy_controller.datetime", fake)
    return _fix


# ---------------------------------------------------------------------------
# initialize()
# ---------------------------------------------------------------------------

class TestInitialize:
    def test_schedules_run_every_with_default_interval(self):
        ctrl = ChargePolicyController.__new__(ChargePolicyController)
        ctrl.args = _ARGS.copy()
        ctrl.log = MagicMock()
        ctrl.run_every = MagicMock()
        ctrl.initialize()
        ctrl.run_every.assert_called_once_with(ctrl.manage_charging, "now", 300)


    def test_schedules_run_every_with_custom_interval(self):
        ctrl = ChargePolicyController.__new__(ChargePolicyController)
        ctrl.args = {**_ARGS, "check_interval_seconds": "120"}
        ctrl.log = MagicMock()
        ctrl.run_every = MagicMock()
        ctrl.initialize()
        ctrl.run_every.assert_called_once_with(ctrl.manage_charging, "now", 120)


# ---------------------------------------------------------------------------
# Actively charging (IN_USE)
# ---------------------------------------------------------------------------

class TestActivelyCharging:
    # Base states: load (5.0 + 2.0 = 7.0 kW) exceeds default solar (3.0 kW).
    _states = {
        "sensor.charger_state": "In Use",
        "sensor.charger_plug":  "Plugged In",
        "sensor.charger_power": "5.0",
        "sensor.solar":         "3.0",
        "sensor.battery":       "0.0",
        "sensor.home_load":     "2.0",
    }


    # ── Peak hours (15:00–23:59): stop when load > solar ──
    def test_stops_peak_charger_exceeds_solar(self, freeze):
        """Charger draw alone exceeds solar during peak hours."""
        freeze(_PEAK)
        ctrl, svc = _make({**self._states, "sensor.charger_power": "5.0", "sensor.solar": "3.0"})
        ctrl.manage_charging({})
        svc.assert_called_once_with("button/press", entity_id="button.charger_off")


    def test_stops_peak_total_load_exceeds_solar(self, freeze):
        """Home + charger total exceeds solar during peak hours."""
        freeze(_PEAK)
        # charger=1.5, home=2.0 → total=3.5 > solar=3.0
        ctrl, svc = _make({**self._states, "sensor.charger_power": "1.5", "sensor.solar": "3.0"})
        ctrl.manage_charging({})
        svc.assert_called_once_with("button/press", entity_id="button.charger_off")


    def test_no_action_peak_solar_covers_all_load(self, freeze):
        """Solar covers all load during peak hours — no stop."""
        freeze(_PEAK)
        ctrl, svc = _make({**self._states, "sensor.charger_power": "1.0", "sensor.home_load": "2.0", "sensor.solar": "10.0"})
        ctrl.manage_charging({})
        svc.assert_not_called()


    def test_no_stop_outside_peak_and_past_window_start(self, freeze):
        """Off-peak and past window start — load > solar alone does not stop the session."""
        freeze(_AFTER_WINDOW)  # 14:00 — not in 15:00–23:59 peak, and 14:00 ≥ 06:30 window start
        ctrl, svc = _make({**self._states, "sensor.charger_power": "5.0", "sensor.solar": "3.0"})
        ctrl.manage_charging({})
        svc.assert_not_called()


    # ── Before charge window start: stop when load > solar ──
    def test_stops_before_window_start_load_exceeds_solar(self, freeze):
        """Load exceeds solar before the charge window opens — stops session."""
        freeze(_BEFORE_WINDOW)  # 06:00, before 06:30 window start
        ctrl, svc = _make({**self._states, "sensor.charger_power": "5.0", "sensor.solar": "3.0"})
        ctrl.manage_charging({})
        svc.assert_called_once_with("button/press", entity_id="button.charger_off")


    # ── Within window, off-peak ──
    def test_no_action_within_window_solar_covers_load(self, freeze):
        """Solar covers all load during the charge window — session continues."""
        freeze(_WITHIN_WINDOW)
        ctrl, svc = _make({**self._states, "sensor.charger_power": "1.0", "sensor.home_load": "2.0", "sensor.solar": "10.0"})
        ctrl.manage_charging({})
        svc.assert_not_called()


    def test_no_action_within_window_load_exceeds_solar_off_peak(self, freeze):
        """Load exceeds solar but we're within the window and off-peak — session continues."""
        freeze(_WITHIN_WINDOW)  # 07:00 ≥ 06:30, not peak hours
        ctrl, svc = _make({**self._states, "sensor.charger_power": "5.0", "sensor.solar": "6.0"})
        ctrl.manage_charging({})
        svc.assert_not_called()


# ---------------------------------------------------------------------------
# Plugged in but not charging (NOT_CHARGING + PLUGGED_IN)
# ---------------------------------------------------------------------------

class TestPluggedInNotCharging:
    _states = {
        "sensor.charger_state": "Not Charging",
        "sensor.charger_plug":  "Plugged In",
        "sensor.solar":         "3.0",
        "sensor.battery":       "0.0",
        "sensor.charger_power": "0.0",
        "sensor.home_load":     "1.0",
    }

    def test_starts_session_within_charge_window(self, freeze):
        """Within charge window — start a session."""
        freeze(_WITHIN_WINDOW)
        ctrl, svc = _make(self._states)
        ctrl.manage_charging({})
        svc.assert_called_once_with("button/press", entity_id="button.charger_on")


    def test_no_start_before_charge_window_start(self, freeze):
        """Before charge window start — no start."""
        freeze(_BEFORE_WINDOW)
        ctrl, svc = _make(self._states)
        ctrl.manage_charging({})
        svc.assert_not_called()


    def test_no_start_after_charge_window_end(self, freeze):
        """After charge window end — no start."""
        freeze(_AFTER_WINDOW)
        ctrl, svc = _make(self._states)
        ctrl.manage_charging({})
        svc.assert_not_called()


    def test_starts_regardless_of_battery_state(self, freeze):
        """Battery discharge state is not evaluated — session starts within window."""
        freeze(_WITHIN_WINDOW)
        ctrl, svc = _make({**self._states, "sensor.solar": "0.1", "sensor.battery": "1.5"})
        ctrl.manage_charging({})
        svc.assert_called_once_with("button/press", entity_id="button.charger_on")


    def test_no_start_when_unplugged(self, freeze):
        """Cable not plugged in — no start."""
        freeze(_WITHIN_WINDOW)
        ctrl, svc = _make({**self._states, "sensor.charger_plug": "Unplugged"})
        ctrl.manage_charging({})
        svc.assert_not_called()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_logs_error_on_missing_entity(self, freeze):
        """Missing entity raises ValueError which is caught and logged."""
        freeze(_WITHIN_WINDOW)
        ctrl, svc = _make({})
        ctrl.manage_charging({})
        logged = ctrl.log.call_args
        assert logged.kwargs.get("level") == "ERROR"
        svc.assert_not_called()


    def test_logs_error_on_unavailable_sensor(self, freeze):
        """Unavailable sensor state is caught and logged."""
        freeze(_WITHIN_WINDOW)
        ctrl, svc = _make({
            "sensor.charger_state": "In Use",
            "sensor.charger_plug":  "Plugged In",
            "sensor.solar":         "unavailable",
            "sensor.battery":       "0.0",
        })
        
        ctrl.manage_charging({})
        logged = ctrl.log.call_args
        assert logged.kwargs.get("level") == "ERROR"
        svc.assert_not_called()
