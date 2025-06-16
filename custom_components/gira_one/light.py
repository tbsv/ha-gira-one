"""Platform for Gira One Light entities."""

import logging
from typing import Any, Dict

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_COLOR_TEMP_KELVIN,
    ATTR_HS_COLOR,
    ATTR_RGB_COLOR,
    ATTR_WHITE,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.util.color as color_util


from .api import GiraApiClient
from .const import (
    DOMAIN,
    DATA_API_CLIENT,
    DATA_UI_CONFIG,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    LIGHT,
    DP_ON_OFF,
    DP_BRIGHTNESS,
    DP_COLOR_TEMPERATURE,
    DP_RED,
    DP_GREEN,
    DP_BLUE,
    DP_WHITE,
)
from . import SIGNAL_DATA_UPDATE, SIGNAL_UI_CONFIG_CHANGED  # Import signals

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira Light entities from a config entry."""
    api_client: GiraApiClient = hass.data[DOMAIN][config_entry.entry_id][
        DATA_API_CLIENT
    ]
    ui_config: Dict[str, Any] = hass.data[DOMAIN][config_entry.entry_id][DATA_UI_CONFIG]

    entities = []
    gira_functions = ui_config.get("functions", [])

    for function in gira_functions:
        function_type = function.get("functionType")
        platform = GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_type)

        if platform == LIGHT:
            entities.append(GiraLight(config_entry, api_client, function))
            _LOGGER.info(
                "Adding Gira Light: %s (UID: %s)",
                function.get("displayName"),
                function.get("uid"),
            )

    async_add_entities(entities)

    # Listener for UI config changes (e.g., if devices are added/removed in Gira GPA)
    # This is a simplified handler; a more robust one might compare old/new configs.
    @callback
    def _async_reload_entities():
        _LOGGER.info(
            "Gira UI config changed, attempting to reload light entities for %s.",
            config_entry.entry_id,
        )
        # This typically involves hass.async_create_task(hass.config_entries.async_reload(config_entry.entry_id))
        # For now, we'll just log. A full reload would be managed by the __init__.py service callback handler.
        # If entities are just being updated, their own state update mechanism handles it.

    config_entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            f"{SIGNAL_UI_CONFIG_CHANGED}_{config_entry.entry_id}",
            _async_reload_entities,
        )
    )


class GiraLight(LightEntity):
    """Representation of a Gira Light."""

    _attr_has_entity_name = True  # Use Gira's displayName as the entity name part
    _attr_should_poll = False  # Updates are pushed via callbacks

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: Dict[str, Any],
    ) -> None:
        """Initialize the Gira Light."""
        self._config_entry_id = config_entry.entry_id
        self._api = api_client
        self._function_data = function_data

        self._attr_unique_id = function_data["uid"]
        self._attr_name = function_data.get(
            "displayName", f"Gira Light {self._attr_unique_id}"
        )
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, self.unique_id)
            },  # Links to the function itself as a device
            name=self.name,
            manufacturer="Gira",
            model=function_data.get(
                "functionType", "Unknown Gira Function"
            ),  # e.g. de.gira.schema.functions.KNX.Light
            model_id=function_data.get(
                "uid"
            ),
            via_device=(
                DOMAIN,
                config_entry.unique_id or config_entry.data[CONF_HOST],
            ),  # Links to the main Gira One server
        )

        self._data_points: Dict[str, Dict[str, Any]] = {
            dp["name"]: dp for dp in function_data.get("dataPoints", [])
        }
        # Example: self._data_points = {"OnOff": {"uid": "a02a", ...}, "Brightness": {"uid": "a02b", ...}}

        self._attr_is_on = None  # Initially unknown
        self._attr_brightness = None
        self._attr_color_temp_kelvin = None
        self._attr_hs_color = None
        # ... other color attributes

        self._update_supported_color_modes_and_features()

        # Initial state fetch (can be slow if many entities)
        # Callbacks will update state later.
        # This can be made part of async_added_to_hass
        # For now, values will be None until first callback or manual update.
        _LOGGER.debug(
            "Initialized Gira Light %s with data points: %s",
            self.name,
            self._data_points.keys(),
        )

    def _update_supported_color_modes_and_features(self) -> None:
        """Determine supported color modes and features based on available data points."""
        supported_color_modes = set()
        supported_features = LightEntityFeature(0)

        if DP_ON_OFF in self._data_points:
            supported_color_modes.add(ColorMode.ONOFF)
            # Basic on/off implies no other features unless other DPs exist

        if DP_BRIGHTNESS in self._data_points:
            supported_color_modes.add(
                ColorMode.BRIGHTNESS
            )  # Can be on/off + brightness
            supported_features |= (
                LightEntityFeature.TRANSITION
            )  # Assuming Gira might handle transitions

        if DP_COLOR_TEMPERATURE in self._data_points:
            supported_color_modes.add(ColorMode.COLOR_TEMP)

        if (
            DP_RED in self._data_points
            and DP_GREEN in self._data_points
            and DP_BLUE in self._data_points
        ):
            # Could be RGB or HS, HA prefers HS for color wheel usually
            supported_color_modes.add(ColorMode.HS)  # Or RGBW/RGBWW if white DPs exist
            # If DP_WHITE exists, could indicate RGBW.
            # Check channelType for "DimmerRGBW" or "DimmerWhite"
            channel_type = self._function_data.get("channelType", "")
            if "RGBW" in channel_type and DP_WHITE in self._data_points:
                supported_color_modes.add(
                    ColorMode.RGBW
                )  # If a dedicated white channel exists
            else:
                supported_color_modes.add(ColorMode.RGB)

        # If only ONOFF is supported, but BRIGHTNESS mode was added, remove ONOFF.
        if len(supported_color_modes) > 1 and ColorMode.ONOFF in supported_color_modes:
            if (
                ColorMode.BRIGHTNESS in supported_color_modes
                or ColorMode.COLOR_TEMP in supported_color_modes
                or ColorMode.HS in supported_color_modes
                or ColorMode.RGB in supported_color_modes
                or ColorMode.RGBW in supported_color_modes
            ):
                supported_color_modes.remove(ColorMode.ONOFF)

        if not supported_color_modes:  # Fallback if somehow none detected
            _LOGGER.warning(
                "No color modes detected for light %s, defaulting to ON/OFF", self.name
            )
            supported_color_modes.add(ColorMode.ONOFF)

        self._attr_supported_color_modes = supported_color_modes
        self._attr_supported_features = supported_features

        # Determine primary color mode if multiple are supported (e.g. Brightness vs OnOff)
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
        elif ColorMode.ONOFF in supported_color_modes:
            self._attr_color_mode = ColorMode.ONOFF
        else:  # Should not happen due to fallback
            self._attr_color_mode = ColorMode.UNKNOWN

        _LOGGER.debug(
            "Light %s: Supported Modes: %s, Current Mode: %s, Features: %s",
            self.name,
            self._attr_supported_color_modes,
            self._attr_color_mode,
            self._attr_supported_features,
        )

    async def async_added_to_hass(self) -> None:
        """Register for callback when entity is added."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DATA_UPDATE}_{self._config_entry_id}",  # Listen to signals for this specific config entry
                self._handle_value_update,
            )
        )
        # Request initial state for all relevant data points
        # This can be optimized to a single call if get_value supported multiple UIDs in one go,
        # or if we fetch values for the whole function UID.
        try:
            _LOGGER.debug(
                "Requesting initial state for light function UID %s", self.unique_id
            )
            # The API GET /api/values/<uid> where UID can be a function
            # returns all data point values for that function.
            # Response: {"values": [{"uid": "<dp_uid>", "value": "<val>"}]}
            data = await self._api.get_value(self.unique_id)
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_value(
                        dp_value_info["uid"], dp_value_info["value"]
                    )
                self.async_write_ha_state()
                _LOGGER.debug("Initial state updated for light %s", self.name)
        except Exception as e:
            _LOGGER.error(
                "Error fetching initial state for light %s (UID: %s): %s",
                self.name,
                self.unique_id,
                e,
            )

    @callback
    def _handle_value_update(self, dp_uid: str, value: Any) -> None:
        """Handle a value update from the Gira callback."""
        _LOGGER.debug(
            "Light %s received value update for DP UID %s: %s", self.name, dp_uid, value
        )
        if self._update_state_from_value(dp_uid, value):
            self.async_write_ha_state()

    def _update_state_from_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal state attributes based on a data point UID and its new value. Returns True if state changed."""
        changed = False
        original_is_on = self._attr_is_on
        original_brightness = self._attr_brightness
        original_hs_color = self._attr_hs_color
        original_color_temp = self._attr_color_temp_kelvin

        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                _LOGGER.debug(
                    "Matching DP UID %s with DP Name %s for light %s",
                    dp_uid_updated,
                    dp_name,
                    self.name,
                )
                parsed_value = self._parse_value(value, dp_name)
                if dp_name == DP_ON_OFF:
                    self._attr_is_on = bool(int(parsed_value))
                    if self._attr_is_on != original_is_on:
                        changed = True
                elif dp_name == DP_BRIGHTNESS:
                    # Gira brightness 0-100, HA 0-255
                    self._attr_brightness = (
                        int(float(parsed_value) * 2.55)
                        if parsed_value is not None
                        else None
                    )
                    if self._attr_brightness != original_brightness:
                        changed = True
                elif (
                    dp_name == DP_COLOR_TEMPERATURE
                ):  # Value in Kelvin (DimmerWhite channel)
                    self._attr_color_temp_kelvin = (
                        int(parsed_value) if parsed_value is not None else None
                    )
                    if self._attr_color_temp_kelvin != original_color_temp:
                        changed = True
                # Add RGB/HS handling
                # This needs careful consideration as Gira provides R,G,B,W separately.
                # HA's hs_color needs to be derived.
                # For simplicity now, we'll assume direct control if these DPs are available.
                # A more robust solution would convert Gira's R,G,B (0-100%) to HA's HS (or RGB 0-255).
                # And when setting, convert HA's HS to Gira's R,G,B.
                # For now, just logging.
                elif dp_name in [DP_RED, DP_GREEN, DP_BLUE, DP_WHITE]:
                    _LOGGER.debug(
                        "Color component %s updated to %s. HS/RGB conversion needed.",
                        dp_name,
                        parsed_value,
                    )
                    # Potentially update an internal _rgbw_state and then derive _attr_hs_color or _attr_rgb[w]_color
                    # For now, we won't directly map these to a single HA color attribute on read,
                    # as it requires knowing all components.
                    # On write (turn_on), we'll handle it.
                    changed = True  # Assume change for now

                if changed:
                    _LOGGER.debug(
                        "State changed for light %s: OnOff=%s, Brightness=%s, ColorTemp=%s",
                        self.name,
                        self._attr_is_on,
                        self._attr_brightness,
                        self._attr_color_temp_kelvin,
                    )
                return changed  # Found and processed the data point

        _LOGGER.warning(
            "DP UID %s not found in known data points for light %s",
            dp_uid_updated,
            self.name,
        )
        return False

    def _parse_value(self, value: Any, dp_name: str) -> Any:
        """Parse value from string if necessary, based on expected type."""
        # Values from API can be strings e.g. "1", "70.0"
        # TODO: Determine actual types from channel definitions in PDF section 9.2
        # e.g. OnOff is Binary, Brightness is Percent, Color-Temperature is Float
        try:
            if dp_name in [DP_ON_OFF]:
                return int(value)
            if dp_name in [
                DP_BRIGHTNESS,
                DP_RED,
                DP_GREEN,
                DP_BLUE,
                DP_WHITE,
            ]:  # Percentages
                return float(value)  # Keep as 0-100 for now, convert to 0-255 later
            if dp_name in [DP_COLOR_TEMPERATURE]:  # Float
                return float(value)
        except (ValueError, TypeError) as e:
            _LOGGER.warning(
                "Could not parse value '%s' for data point %s: %s", value, dp_name, e
            )
            return None
        return value

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the light on."""
        _LOGGER.debug("Turning on light %s with kwargs: %s", self.name, kwargs)
        payloads = []

        # OnOff datapoint
        on_off_dp_uid = self._data_points.get(DP_ON_OFF, {}).get("uid")
        if not on_off_dp_uid:
            _LOGGER.error("OnOff data point UID not found for light %s", self.name)
            return

        # Always send On command if turning on, unless it's only a color/brightness change for an already on light
        # For simplicity, we always send "On" and then other attributes.
        # Some Gira functions might turn on automatically when brightness > 0.
        payloads.append({"uid": on_off_dp_uid, "value": 1})  # 1 for ON

        # Brightness
        if ATTR_BRIGHTNESS in kwargs and DP_BRIGHTNESS in self._data_points:
            ha_brightness = kwargs[ATTR_BRIGHTNESS]  # 0-255
            gira_brightness = round((ha_brightness / 255) * 100)  # Convert to 0-100
            dp_uid = self._data_points[DP_BRIGHTNESS]["uid"]
            payloads.append({"uid": dp_uid, "value": gira_brightness})
            self._attr_brightness = ha_brightness  # Optimistic update

        # Color Temperature
        if (
            ATTR_COLOR_TEMP_KELVIN in kwargs
            and DP_COLOR_TEMPERATURE in self._data_points
        ):
            kelvin = kwargs[ATTR_COLOR_TEMP_KELVIN]
            dp_uid = self._data_points[DP_COLOR_TEMPERATURE]["uid"]
            payloads.append({"uid": dp_uid, "value": kelvin})
            self._attr_color_temp_kelvin = kelvin  # Optimistic update

        # HS Color (convert to RGB for Gira if it uses RGB data points)
        if (
            ATTR_HS_COLOR in kwargs
            and DP_RED in self._data_points
            and DP_GREEN in self._data_points
            and DP_BLUE in self._data_points
        ):
            hs_color = kwargs[ATTR_HS_COLOR]
            rgb_color = color_util.color_hs_to_RGB(*hs_color)  # HA returns 0-255 RGB

            # Convert 0-255 RGB to Gira's 0-100 percentage for R,G,B
            gira_r = round((rgb_color[0] / 255) * 100)
            gira_g = round((rgb_color[1] / 255) * 100)
            gira_b = round((rgb_color[2] / 255) * 100)

            payloads.append({"uid": self._data_points[DP_RED]["uid"], "value": gira_r})
            payloads.append(
                {"uid": self._data_points[DP_GREEN]["uid"], "value": gira_g}
            )
            payloads.append({"uid": self._data_points[DP_BLUE]["uid"], "value": gira_b})
            self._attr_hs_color = hs_color  # Optimistic update

            # Handle White channel if ATTR_WHITE or ATTR_RGBW_COLOR was used
            # and if it's an RGBW light
            if (
                (ATTR_WHITE in kwargs or ATTR_RGB_COLOR in kwargs)
                and DP_WHITE in self._data_points
                and self.supported_color_modes
                and ColorMode.RGBW in self.supported_color_modes
            ):
                # If ATTR_WHITE is present, it's for the W channel of RGBW (0-255)
                # If ATTR_RGBW_COLOR is used, the 4th component is W
                ha_white_val = kwargs.get(
                    ATTR_WHITE
                )  # Could come from ATTR_WHITE directly
                if (
                    ATTR_RGB_COLOR in kwargs and len(kwargs[ATTR_RGB_COLOR]) == 4
                ):  # Check if it's RGBW
                    # This scenario is less common for HS mode, but for completeness
                    # If HS is set, RGB is derived. If RGBW is the mode, this might be different.
                    # This part needs more careful thought on HA color mode handling.
                    # For now, if ATTR_WHITE is explicitly passed, use it.
                    pass  # Complicated, handle based on current color_mode

                if ha_white_val is not None:
                    gira_white = round((ha_white_val / 255) * 100)
                    payloads.append(
                        {"uid": self._data_points[DP_WHITE]["uid"], "value": gira_white}
                    )

        if payloads:
            _LOGGER.debug("Setting light %s with payloads: %s", self.name, payloads)
            try:
                if (
                    len(payloads) == 1
                    and payloads[0]["uid"] == on_off_dp_uid
                    and payloads[0]["value"] == 1
                    and len(kwargs) == 0
                ):
                    # Only turning on, no other attributes
                    await self._api.set_value(on_off_dp_uid, 1)
                else:
                    await self._api.set_multiple_values(payloads)
                self._attr_is_on = True  # Optimistic update
            except Exception as e:
                _LOGGER.error("Error turning on light %s: %s", self.name, e)
                # Revert optimistic updates if API call fails? Or wait for callback.
                return  # Don't write HA state if error

        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the light off."""
        on_off_dp_uid = self._data_points.get(DP_ON_OFF, {}).get("uid")
        if not on_off_dp_uid:
            _LOGGER.error("OnOff data point UID not found for light %s", self.name)
            return

        _LOGGER.debug("Turning off light %s", self.name)
        try:
            await self._api.set_value(on_off_dp_uid, 0)  # 0 for OFF
            self._attr_is_on = False  # Optimistic update
        except Exception as e:
            _LOGGER.error("Error turning off light %s: %s", self.name, e)
            return

        self.async_write_ha_state()

    # TODO: Implement other methods like async_update if polling is ever needed.
    # For now, callbacks handle updates.
