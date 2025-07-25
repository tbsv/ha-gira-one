"""Platform for Gira One Light entities."""

import logging
from typing import Any, Dict

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.color as color_util

from .api import GiraApiClient
from .const import (
    DATA_API_CLIENT,
    DATA_UI_CONFIG,
    DP_BRIGHTNESS,
    DP_BLUE,
    DP_COLOR_TEMPERATURE,
    DP_GREEN,
    DP_ON_OFF,
    DP_RED,
    DP_WHITE,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    LIGHT,
)
from .entity import GiraOneEntity  # Import the new base class

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira Light entities from a config entry."""
    api_client: GiraApiClient = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_API_CLIENT
    ]
    ui_config: Dict[str, Any] = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_UI_CONFIG
    ]

    entities = []
    for function_data in ui_config.get("functions", []):
        if (
            GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_data.get("functionType"))
            == LIGHT
        ):
            entities.append(GiraLight(config_entry, api_client, function_data))
            _LOGGER.info(
                "Adding Gira Light: %s (UID: %s)",
                function_data.get("displayName"),
                function_data.get("uid"),
            )
    async_add_entities(entities)


class GiraLight(GiraOneEntity, LightEntity):
    """Representation of a Gira Light."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: Dict[str, Any],
    ) -> None:
        """Initialize the Gira Light."""
        super().__init__(config_entry, api_client, function_data)

        # Light-specific attributes
        self._attr_is_on = None
        self._attr_brightness = None
        self._attr_color_temp_kelvin = None
        self._attr_hs_color = None

        self._update_supported_color_modes_and_features()

    def _update_supported_color_modes_and_features(self) -> None:
        """Determine supported color modes and features based on available data points."""
        supported_color_modes = set()
        supported_features = LightEntityFeature(0)

        if self._has_dp(DP_ON_OFF):
            supported_color_modes.add(ColorMode.ONOFF)

        if self._has_dp(DP_BRIGHTNESS):
            supported_color_modes.add(ColorMode.BRIGHTNESS)

        if self._has_dp(DP_COLOR_TEMPERATURE):
            supported_color_modes.add(ColorMode.COLOR_TEMP)

        if self._has_dp(DP_RED) and self._has_dp(DP_GREEN) and self._has_dp(DP_BLUE):
            supported_color_modes.add(ColorMode.HS)
            channel_type = self._function_data.get("channelType", "")
            if "RGBW" in channel_type and self._has_dp(DP_WHITE):
                supported_color_modes.add(ColorMode.RGBW)
            else:
                supported_color_modes.add(ColorMode.RGB)

        if len(supported_color_modes) > 1 and ColorMode.ONOFF in supported_color_modes:
            supported_color_modes.remove(ColorMode.ONOFF)

        if not supported_color_modes:
            supported_color_modes.add(ColorMode.ONOFF)

        self._attr_supported_color_modes = supported_color_modes
        self._attr_supported_features = supported_features

        # Determine primary color mode
        # (This logic could be simplified, but is functional)
        if ColorMode.HS in supported_color_modes:
            self._attr_color_mode = ColorMode.HS
        elif ColorMode.RGBW in supported_color_modes:
            self._attr_color_mode = ColorMode.RGBW
        elif ColorMode.RGB in supported_color_modes:
            self._attr_color_mode = ColorMode.RGB
        elif ColorMode.COLOR_TEMP in supported_color_modes:
            self._attr_color_mode = ColorMode.COLOR_TEMP
        elif ColorMode.BRIGHTNESS in supported_color_modes:
            self._attr_color_mode = ColorMode.BRIGHTNESS
        else:
            self._attr_color_mode = ColorMode.ONOFF

    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the light."""
        try:
            data = await self._api.get_value(self.unique_id)
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_dp_value(
                        dp_value_info["uid"], dp_value_info["value"]
                    )
        except Exception as e:
            _LOGGER.error("Error fetching initial state for light %s: %s", self.name, e)

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal state. Returns True if state changed."""
        changed = False
        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                try:
                    if dp_name == DP_ON_OFF:
                        is_on = bool(int(value))
                        if self._attr_is_on != is_on:
                            self._attr_is_on = is_on
                            changed = True
                    elif dp_name == DP_BRIGHTNESS:
                        # Gira 0-100 -> HA 0-255
                        brightness = int(float(value) * 2.55)
                        if self._attr_brightness != brightness:
                            self._attr_brightness = brightness
                            changed = True
                    elif dp_name == DP_COLOR_TEMPERATURE:
                        color_temp = int(float(value))
                        if self._attr_color_temp_kelvin != color_temp:
                            self._attr_color_temp_kelvin = color_temp
                            changed = True
                    # Note: Reading RGB values and converting to HS is complex
                    # as it requires all 3 values. We handle this on write.
                except (ValueError, TypeError):
                    return False
                return changed
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        payloads = []

        # Always send ON command
        if self._has_dp(DP_ON_OFF):
            payloads.append({"uid": self._get_dp_uid(DP_ON_OFF), "value": 1})

        # Brightness
        if ATTR_BRIGHTNESS in kwargs and self._has_dp(DP_BRIGHTNESS):
            ha_brightness = kwargs[ATTR_BRIGHTNESS]
            gira_brightness = round((ha_brightness / 255) * 100)
            payloads.append(
                {"uid": self._get_dp_uid(DP_BRIGHTNESS), "value": gira_brightness}
            )

        # Color Temperature
        if ATTR_COLOR_TEMP_KELVIN in kwargs and self._has_dp(DP_COLOR_TEMPERATURE):
            payloads.append(
                {
                    "uid": self._get_dp_uid(DP_COLOR_TEMPERATURE),
                    "value": kwargs[ATTR_COLOR_TEMP_KELVIN],
                }
            )

        # HS Color
        if ATTR_HS_COLOR in kwargs and self._has_dp(DP_RED):
            rgb_color = color_util.color_hs_to_RGB(*kwargs[ATTR_HS_COLOR])
            payloads.append(
                {
                    "uid": self._get_dp_uid(DP_RED),
                    "value": round((rgb_color[0] / 255) * 100),
                }
            )
            payloads.append(
                {
                    "uid": self._get_dp_uid(DP_GREEN),
                    "value": round((rgb_color[1] / 255) * 100),
                }
            )
            payloads.append(
                {
                    "uid": self._get_dp_uid(DP_BLUE),
                    "value": round((rgb_color[2] / 255) * 100),
                }
            )

        if payloads:
            # Use the more efficient multi-value call if available
            await self._api.set_multiple_values(payloads)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        if self._has_dp(DP_ON_OFF):
            await self._send_command(DP_ON_OFF, 0)
