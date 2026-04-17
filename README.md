# Smart Charging Controller AppDaemon for FranklinWH + ChargePoint  (Home Assistant)

An [AppDaemon](https://appdaemon.readthedocs.io/en/latest/) app that automatically starts and stops a **ChargePoint** home EV charger based on real-time solar production and battery state from a **FranklinWH** energy system, both surfaced through **Home Assistant**.

## What it does

The app runs every 5 minutes and applies this decision logic:

| Condition | Action |
|---|---|
| Car battery is fully charged | Stop the charging session |
| Charging past the charge window end (default 13:00) | Stop the charging session |
| Charging during PG&E peak hours (3:00pm – midnight) and charger draw ≥ solar output, or total home load ≥ solar output | Stop the charging session |
| Charging with solar < 0.5 kW and battery is discharging | Stop (prevents draining the home battery overnight) |
| Plugged in but not charging, solar/battery conditions are healthy, and time is within the charge window (default 06:30–13:00) | Start a charging session |

## Prerequisites

- Home Assistant with the **ChargePoint** and **FranklinWH** integrations installed and reporting sensor data.
- The **AppDaemon 4** add-on installed in Home Assistant (HA → Add-ons → AppDaemon 4).

---

## Installation

### Phase 1 — Configure AppDaemon

When AppDaemon starts for the first time it creates a minimal `appdaemon.yaml`. Open that file (via the HA File Editor add-on or SSH) and populate it:

> **Note (v0.15.0+):** The AppDaemon configuration folder moved from `/config/appdaemon/` to `/addon_configs/a0d7b954_appdaemon/`. If the folder appears empty, check that new location. The `secrets:` key must point to `/homeassistant/secrets.yaml`.

```yaml
secrets: /homeassistant/secrets.yaml

appdaemon:
  time_zone: America/Los_Angeles   # match your HA time zone
  latitude: 0.0                    # match your HA location
  longitude: 0.0
  elevation: 0
  plugins:
    HASS:
      type: hass
      ha_url: http://homeassistant:8123
      token: !secret home_assistant_access_token
```

1. HA → Profile (bottom-left avatar) → **Long-Lived Access Tokens** → **Create Token**
2. Name it `appdaemon` and copy the generated value.
3. Add it to `/homeassistant/secrets.yaml`:
   ```yaml
   home_assistant_access_token: <paste token here>
   ```

Restart the AppDaemon add-on after saving.

---

### Phase 2 — Create the app directory

Create the following directory:

```
/homeassistant/appdaemon/apps/
```

Then symlink it so AppDaemon picks it up from its config location:

```bash
# Remove the placeholder apps folder AppDaemon created on first run
rm -rf /addon_configs/a0d7b954_appdaemon/apps

# Symlink to the new location
ln -s /homeassistant/appdaemon/apps /addon_configs/a0d7b954_appdaemon/apps
```

When done, the full directory tree should look like:

```
/addon_configs/a0d7b954_appdaemon/
  appdaemon.yaml
  apps/  →  /homeassistant/appdaemon/apps/
    apps.yaml
    charge_policy_controller/
      charge_policy_controller.py
      models.py
```

---

### Phase 3 — Deploy the app files

Copy these two files from this repository into `/homeassistant/appdaemon/apps/charge_policy_controller/`:

- `charge_policy_controller.py`
- `models.py`

Copy `apps.yaml` from this repository to `/homeassistant/appdaemon/apps/apps.yaml`.
If an `apps.yaml` already exists there, merge the `charge_policy_controller:` block into it.

---

### Phase 4 — Add ChargePoint secrets

The ChargePoint entity IDs are stored in `secrets.yaml` rather than `apps.yaml` to keep device-specific identifiers out of version control. Add the following entries to `/homeassistant/secrets.yaml`, replacing `<device_id>` with your charger's device segment:

```yaml
charger_device_state: sensor.cp_home_<device_id>_charger_state
charger_plug_state:   sensor.cp_home_<device_id>_charging_cable
charger_power_output: sensor.cp_home_<device_id>_power_output
charger_turn_on:      button.cp_home_<device_id>_start_charging
charger_turn_off:     button.cp_home_<device_id>_stop_charging
```

**Finding your device ID:**
HA → **Settings** → **Devices & Services** → **ChargePoint** → click your charger.
The entity names shown contain the device-specific segment (e.g. `0eda73_cph50`).

---

### Phase 5 — Verify

1. Restart (or reload) the AppDaemon add-on.
2. Open the log: HA → **Add-ons** → **AppDaemon 4** → **Log**
3. Look for this line confirming the app loaded:
   ```
   ChargePolicyController initialized — evaluating every 300 s
   ```
4. Within 5 minutes a second entry should appear (e.g. `No action required.`), confirming the scheduler is running.

**If errors appear instead, check:**
- Entity IDs in `secrets.yaml` match exactly what HA reports under the ChargePoint device.
- The `home_assistant_access_token` secret is valid and has not expired.
- `apps.yaml` indentation is correct (YAML is whitespace-sensitive).

---

## Configuration

All timing and entity configuration lives in `apps.yaml`. No Python code needs to change.

| Key | Default | Description |
|---|---|---|
| `check_interval_seconds` | `300` | How often to evaluate charging conditions (seconds) |
| `charge_window_start` | `06:30:00` | Earliest time a session may start (HH:MM:SS) |
| `charge_window_end` | `13:00:00` | Latest time a session may run (HH:MM:SS) |
| `solar_power_output` | — | FranklinWH solar production entity |
| `solar_battery_output` | — | FranklinWH battery use entity |
| `home_load` | — | FranklinWH home load entity |
| `charger_*` | via `!secret` | ChargePoint sensor and button entities |

## Logs

AppDaemon writes all log output to:

- HA → **Add-ons** → **AppDaemon 4** → **Log**
- File: `/addon_configs/a0d7b954_appdaemon/appdaemon.log`

Errors (unhandled exceptions with full tracebacks) are logged at `ERROR` level.
