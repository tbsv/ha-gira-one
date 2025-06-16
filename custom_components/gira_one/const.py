"""Constants for the Gira One integration."""

DOMAIN = "gira_one"

# Platforms
LIGHT = "light"
COVER = "cover"
CLIMATE = "climate"
PLATFORMS = [LIGHT, COVER, CLIMATE]

# Configuration and options
CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"

# API constants
API_VERSION = "v2" # As specified in the PDF
CLIENT_URN_PREFIX = "urn:homeassistant:gira_iot" # To create a unique client ID
DEFAULT_SKIP_CERT_VERIFY = True # Gira API uses self-signed certs

# Data stored in hass.data
DATA_API_CLIENT = "api_client"
DATA_UI_CONFIG = "ui_config"
DATA_COORDINATOR = "coordinator" # For callback updates
DATA_SETUP_LOCK = "setup_lock"
DATA_LISTENERS = "listeners"
DATA_ACCESS_TOKEN = "access_token"


# Supported Gira function types to HA platforms mapping
# Based on PDF section 9.1 Funktionsdefinitionen
GIRA_FUNCTION_TYPE_TO_HA_PLATFORM = {
    "de.gira.schema.functions.Switch": LIGHT, # Switched light
    "de.gira.schema.functions.KNX.Light": LIGHT, # Dimmer
    "de.gira.schema.functions.ColoredLight": LIGHT, # Colored light
    "de.gira.schema.functions.TunableLight": LIGHT, # Tunable white
    "de.gira.schema.functions.Covering": COVER, # Shutter and blind
    "de.gira.schema.functions.SaunaHeating": CLIMATE, # Sauna temperature
    "de.gira.schema.functions.KNX.HeatingCooling": CLIMATE, # Heating and cooling
    "de.gira.schema.functions.KNX.FanCoil": CLIMATE, # KNX air conditioning / fan coil
}

# Datapoint names from channel definitions (PDF section 9.2)
DP_ON_OFF = "OnOff"
DP_BRIGHTNESS = "Brightness"
DP_COLOR_TEMPERATURE = "Color-Temperature" # For TunableLight
DP_RED = "Red"
DP_GREEN = "Green"
DP_BLUE = "Blue"
DP_WHITE = "White" # For DimmerRGBW

# Data Point Names for Cover
DP_POSITION = "Position" # For Covering (Absolutposition)
DP_SLAT_POSITION = "Slat-Position" # For Covering (Lamellenposition)
DP_STEP_UP_DOWN = "Step-Up-Down" # For Covering
DP_UP_DOWN = "Up-Down" # For Covering (Stop is implicit by setting same value again or not continuing)
DP_MOVEMENT = "Movement" # Read-only, indicates if cover is moving

# Data Point Names for Climate (zusätzlich zu bestehenden wie DP_CURRENT_TEMP, DP_TARGET_TEMP)
DP_CURRENT_TEMP = "Current" # For Heating/Cooling
DP_TARGET_TEMP = "Set-Point" # For Heating/Cooling
DP_OPERATION_MODE = "Mode" # For Heating/Cooling (e.g., heat, cool)
DP_HVAC_ACTION = "Status" # Read-only, current action (e.g., heating, cooling, idle)
DP_HVAC_ON_OFF = "OnOff" # For some heating/cooling systems
DP_HVAC_MODE = "Mode"                       # Gira's HVAC mode (byte value)
DP_HVAC_ACTION_STATUS = "Status"            # Gira's controller status (might contribute to hvac_action)
DP_HVAC_HEATING_ACTIVE = "Heating"          # Binary, is heating subsystem active?
DP_HVAC_COOLING_ACTIVE = "Cooling"          # Binary, is cooling subsystem active?
DP_HVAC_HEAT_COOL_SYSTEM_MODE = "Heat-Cool" # Binary, to switch main system between heat/cool operation (e.g., 2-pipe)
# DP_HVAC_ON_OFF = "OnOff"                  # Master On/Off for climate device

# Gira KNX HVAC Mode constants (based on DPT 20.102)
# These are potential values the 'Mode' data point (DP_HVAC_MODE) might take.
GIRA_KNX_HVAC_MODE_AUTO = 0
GIRA_KNX_HVAC_MODE_COMFORT = 1
GIRA_KNX_HVAC_MODE_STANDBY = 2
GIRA_KNX_HVAC_MODE_ECONOMY = 3  # Night mode
GIRA_KNX_HVAC_MODE_PROTECTION = 4 # Frost/Heat protection

# Fan control data points (for FanCoil functions)
DP_FAN_SPEED = "Fan-Speed" # Byte, e.g., 0=Auto, 1=Low, 2=Medium, 3=High

# Event types from Service Callback
EVENT_TYPE_TEST = "test"
EVENT_TYPE_STARTUP = "startup"
EVENT_TYPE_RESTART = "restart"
EVENT_TYPE_PROJECT_CONFIG_CHANGED = "projectConfigChanged"
EVENT_TYPE_UI_CONFIG_CHANGED = "uiConfigChanged"