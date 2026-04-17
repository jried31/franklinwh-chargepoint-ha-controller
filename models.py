from enum import Enum

# Helper classes
class ChargerDeviceState(Enum):
    FULLY_CHARGED = "Fully Charged"
    NOT_CHARGING = "Not Charging"
    IN_USE = "In Use"
    WAITING = "Waiting"
    AVAILABLE = "Available"

class ChargerPlugState(Enum):
    PLUGGED_IN = "Plugged In"
    NOT_PLUGGED_IN = "Unplugged"

class ChargingStatus(Enum):
    CHARGING = "Charging"
    NOT_CHARGING = "Not Charging"
    AVAILABLE = "Available"

class SolarBatteryState(Enum):
    CHARGING = "charging"
    DISCHARGING = "discharging"
    NOT_CHARGING = "not_charging"
    FULL = "full"
