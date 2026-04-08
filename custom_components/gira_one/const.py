"""Constants for the Gira One integration."""

# Domain
DOMAIN = "gira_one"

# Platforms
LIGHT = "light"
COVER = "cover"
CLIMATE = "climate"
SENSOR = "sensor"
PLATFORMS = [LIGHT, COVER, CLIMATE, SENSOR]

# Configuration
CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"  # noqa: S105

# API constants
API_VERSION = "v2"
CLIENT_URN_PREFIX = "urn:homeassistant:gira_one"

# Data
DATA_API_CLIENT = "api_client"
DATA_UI_CONFIG = "ui_config"
DATA_LOCATION_MAP = "location_map"

# Supported Gira function types to HA platforms mapping
GIRA_FUNCTION_TYPE_TO_HA_PLATFORM = {
    "de.gira.schema.functions.Switch": LIGHT,
    "de.gira.schema.functions.KNX.Light": LIGHT,
    "de.gira.schema.functions.ColoredLight": LIGHT,
    "de.gira.schema.functions.TunableLight": LIGHT,
    "de.gira.schema.functions.Covering": COVER,
    "de.gira.schema.functions.SaunaHeating": CLIMATE,
    "de.gira.schema.functions.KNX.HeatingCooling": CLIMATE,
    "de.gira.schema.functions.KNX.FanCoil": CLIMATE,
}

# --- Data Point Names ---

# Lights
DP_ON_OFF = "OnOff"
DP_BRIGHTNESS = "Brightness"
DP_COLOR_TEMPERATURE = "Color-Temperature"
DP_RED = "Red"
DP_GREEN = "Green"
DP_BLUE = "Blue"
DP_WHITE = "White"

# Cover
DP_POSITION = "Position"
DP_SLAT_POSITION = "Slat-Position"
DP_STEP_UP_DOWN = "Step-Up-Down"
DP_UP_DOWN = "Up-Down"
DP_MOVEMENT = "Movement"

# Climate
DP_CURRENT_TEMP = "Current"
DP_TARGET_TEMP = "Set-Point"
DP_HVAC_ON_OFF = "OnOff"
DP_HVAC_MODE = "Mode"  # Write-only, to set the HVAC mode
DP_HVAC_STATUS_MODE = "Status"  # Read-only, to get the current HVAC mode
DP_HVAC_HEATING_ACTIVE = "Heating"  # Read-only, indicates if heating is active
DP_HVAC_COOLING_ACTIVE = "Cooling"  # Read-only, indicates if cooling is active
DP_FAN_SPEED = "Fan-Speed"  # For FanCoil functions

# --- Presets and Modes ---

# Custom preset name for protection mode
PRESET_PROTECTION = "protection"

# Gira KNX HVAC Mode constants (values for DP_HVAC_MODE and DP_HVAC_STATUS_MODE)
GIRA_KNX_HVAC_MODE_COMFORT = 1
GIRA_KNX_HVAC_MODE_STANDBY = 2
GIRA_KNX_HVAC_MODE_ECONOMY = 3
GIRA_KNX_HVAC_MODE_PROTECTION = 4

# --- Event Types from Service Callback ---

EVENT_TYPE_TEST = "test"
EVENT_TYPE_STARTUP = "startup"
EVENT_TYPE_RESTART = "restart"
EVENT_TYPE_PROJECT_CONFIG_CHANGED = "projectConfigChanged"
EVENT_TYPE_UI_CONFIG_CHANGED = "uiConfigChanged"
