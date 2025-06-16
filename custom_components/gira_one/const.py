"""Constants for the Gira One integration."""

# Domain
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
API_VERSION = "v2"  # As specified in the PDF
CLIENT_URN_PREFIX = "urn:homeassistant:gira_one"  # To create a unique client ID
DEFAULT_SKIP_CERT_VERIFY = True  # Gira IoT API uses self-signed certs

# Data stored in hass.data
DATA_API_CLIENT = "api_client"
DATA_UI_CONFIG = "ui_config"
DATA_COORDINATOR = "coordinator"  # For callback updates
DATA_SETUP_LOCK = "setup_lock"
DATA_LISTENERS = "listeners"
DATA_ACCESS_TOKEN = "access_token"


# Supported Gira function types to HA platforms mapping
GIRA_FUNCTION_TYPE_TO_HA_PLATFORM = {
    "de.gira.schema.functions.Switch": LIGHT,  # Switched light
    "de.gira.schema.functions.KNX.Light": LIGHT,  # Dimmer
    "de.gira.schema.functions.ColoredLight": LIGHT,  # Colored light
    "de.gira.schema.functions.TunableLight": LIGHT,  # Tunable white
    "de.gira.schema.functions.Covering": COVER,  # Shutter and blind
    "de.gira.schema.functions.SaunaHeating": CLIMATE,  # Sauna temperature
    "de.gira.schema.functions.KNX.HeatingCooling": CLIMATE,  # Heating and cooling
    "de.gira.schema.functions.KNX.FanCoil": CLIMATE,  # KNX air conditioning / fan coil
}

# Data Point Names for Lights
DP_ON_OFF = "OnOff"
DP_BRIGHTNESS = "Brightness"  # For Dimmer
DP_COLOR_TEMPERATURE = "Color-Temperature"  # For TunableLight
DP_RED = "Red"  # For DimmerRGBW
DP_GREEN = "Green"  # For DimmerRGBW
DP_BLUE = "Blue"  # For DimmerRGBW
DP_WHITE = "White"  # For DimmerRGBW

# Data Point Names for Cover
DP_POSITION = "Position"  # For Covering (Absolutposition)
DP_SLAT_POSITION = "Slat-Position"  # For Covering (Lamellenposition)
DP_STEP_UP_DOWN = "Step-Up-Down"  # For Covering
DP_UP_DOWN = "Up-Down"  # For Covering (Stop is implicit by setting same value again or not continuing)
DP_MOVEMENT = "Movement"  # Read-only, indicates if cover is moving

# Data Point Names for Climate
DP_CURRENT_TEMP = "Current"  # For Heating/Cooling
DP_TARGET_TEMP = "Set-Point"  # For Heating/Cooling
DP_OPERATION_MODE = "Mode"  # For Heating/Cooling (e.g., heat, cool)
DP_HVAC_ACTION = "Status"  # Read-only, current action (e.g., heating, cooling, idle)
DP_HVAC_ON_OFF = "OnOff"  # For some heating/cooling systems
DP_HVAC_MODE = "Mode"  # Gira's HVAC mode (byte value)
DP_HVAC_ACTION_STATUS = (
    "Status"  # Gira's controller status (might contribute to hvac_action)
)
DP_HVAC_HEATING_ACTIVE = "Heating"  # Binary, is heating subsystem active?
DP_HVAC_COOLING_ACTIVE = "Cooling"  # Binary, is cooling subsystem active?
DP_HVAC_HEAT_COOL_SYSTEM_MODE = "Heat-Cool"  # Binary, to switch main system between heat/cool operation (e.g., 2-pipe)
# DP_HVAC_ON_OFF = "OnOff"                  # Master On/Off for climate device

# Gira KNX HVAC Mode constants
# These are potential values the 'Mode' data point (DP_HVAC_MODE) might take.
GIRA_KNX_HVAC_MODE_AUTO = 0  # Auto mode (not available)
GIRA_KNX_HVAC_MODE_COMFORT = 1  # Comfort mode
GIRA_KNX_HVAC_MODE_STANDBY = 2  # Away mode
GIRA_KNX_HVAC_MODE_ECONOMY = 3  # Night mode
GIRA_KNX_HVAC_MODE_PROTECTION = 4  # Frost/Heat protection

# Fan control data points (for FanCoil functions)
DP_FAN_SPEED = "Fan-Speed"  # Byte, e.g., 0=Auto, 1=Low, 2=Medium, 3=High

# Event types from Service Callback
EVENT_TYPE_TEST = "test"
EVENT_TYPE_STARTUP = "startup"
EVENT_TYPE_RESTART = "restart"
EVENT_TYPE_PROJECT_CONFIG_CHANGED = "projectConfigChanged"
EVENT_TYPE_UI_CONFIG_CHANGED = "uiConfigChanged"
