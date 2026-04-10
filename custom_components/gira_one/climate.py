"""Platform for Gira One Climate entities."""

import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate.const import (
    PRESET_AWAY,
    PRESET_COMFORT,
    PRESET_ECO,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GiraApiClient
from .const import (
    CLIMATE,
    DATA_API_CLIENT,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DP_CURRENT_TEMP,
    DP_HVAC_COOLING_ACTIVE,
    DP_HVAC_HEATING_ACTIVE,
    DP_HVAC_MODE,
    DP_HVAC_ON_OFF,
    DP_HVAC_STATUS_MODE,
    DP_TARGET_TEMP,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    GIRA_KNX_HVAC_MODE_COMFORT,
    GIRA_KNX_HVAC_MODE_ECONOMY,
    GIRA_KNX_HVAC_MODE_PROTECTION,
    GIRA_KNX_HVAC_MODE_STANDBY,
    PRESET_PROTECTION,
)
from .entity import GiraOneEntity

_LOGGER = logging.getLogger(__name__)

GIRA_MODE_TO_HA_PRESET_MAP = {
    GIRA_KNX_HVAC_MODE_COMFORT: PRESET_COMFORT,
    GIRA_KNX_HVAC_MODE_ECONOMY: PRESET_ECO,
    GIRA_KNX_HVAC_MODE_STANDBY: PRESET_AWAY,
    GIRA_KNX_HVAC_MODE_PROTECTION: PRESET_PROTECTION,
}
HA_PRESET_TO_GIRA_MODE_MAP = {v: k for k, v in GIRA_MODE_TO_HA_PRESET_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira Climate entities from a config entry."""
    api_client = hass.data[config_entry.domain][config_entry.entry_id][DATA_API_CLIENT]
    ui_config = hass.data[config_entry.domain][config_entry.entry_id][DATA_UI_CONFIG]
    location_map = hass.data[config_entry.domain][config_entry.entry_id].get(
        DATA_LOCATION_MAP, {}
    )

    entities = []
    for function_data in ui_config.get("functions", []):
        if (
            GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_data.get("functionType"))
            == CLIMATE
        ):
            suggested_area = location_map.get(function_data.get("uid"))
            entities.append(
                GiraClimate(config_entry, api_client, function_data, suggested_area)
            )
            _LOGGER.info(
                "Adding Gira Climate: %s (UID: %s)",
                function_data.get("displayName"),
                function_data.get("uid"),
            )
    async_add_entities(entities)


class GiraClimate(GiraOneEntity, ClimateEntity):
    """Representation of a Gira Climate device."""

    _attr_translation_key = "gira_one"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _enable_turn_on_off_backwards_compatibility = False
    _attr_assumed_state = True

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: dict[str, Any],
        suggested_area: str | None = None,
    ) -> None:
        """Initialize the Gira Climate device."""
        super().__init__(config_entry, api_client, function_data, suggested_area)

        # Climate-specific attributes
        self._attr_current_temperature: float | None = None
        self._attr_target_temperature: float | None = None
        self._attr_hvac_mode: HVACMode | None = None
        self._attr_hvac_action: HVACAction | None = None
        self._attr_preset_mode: str | None = None
        self._attr_preset_modes: list[str] = []

        # Internal states for logic
        self._is_on_dp_value: bool | None = None
        self._current_gira_mode: int | None = None
        self._heating_active_dp_value: bool | None = None
        self._cooling_active_dp_value: bool | None = None
        self._last_active_hvac_mode: HVACMode | None = None

        self._update_supported_attributes()

    def _update_supported_attributes(self) -> None:
        """Determine supported features and preset modes."""
        features = ClimateEntityFeature(0)
        hvac_modes: list[HVACMode] = []

        if self._can_write_dp(DP_TARGET_TEMP):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE

        if self._can_write_dp(DP_HVAC_ON_OFF):
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
            hvac_modes.append(HVACMode.OFF)

        if self._has_dp(DP_HVAC_HEATING_ACTIVE):
            hvac_modes.append(HVACMode.HEAT)
        if self._has_dp(DP_HVAC_COOLING_ACTIVE):
            hvac_modes.append(HVACMode.COOL)

        self._attr_hvac_modes = sorted(set(hvac_modes))

        if self._can_write_dp(DP_HVAC_MODE):
            features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = sorted(set(GIRA_MODE_TO_HA_PRESET_MAP.values()))

        self._attr_supported_features = features

    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the climate device."""
        try:
            data = await self._api.get_value(self.unique_id)
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_dp_value(
                        dp_value_info["uid"], dp_value_info["value"]
                    )
        except Exception as e:
            _LOGGER.exception(
                "Error fetching initial state for climate %s: %s", self._display_name, e
            )

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal state. Returns True if state changed."""
        changed = False
        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                try:
                    if dp_name == DP_CURRENT_TEMP:
                        self._attr_current_temperature = float(value)
                    elif dp_name == DP_TARGET_TEMP:
                        self._attr_target_temperature = float(value)
                    elif dp_name == DP_HVAC_ON_OFF:
                        self._is_on_dp_value = bool(int(value))
                    elif dp_name == DP_HVAC_STATUS_MODE:
                        self._current_gira_mode = int(value)
                    elif dp_name == DP_HVAC_HEATING_ACTIVE:
                        self._heating_active_dp_value = bool(int(value))
                    elif dp_name == DP_HVAC_COOLING_ACTIVE:
                        self._cooling_active_dp_value = bool(int(value))
                    changed = True
                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Could not parse value '%s' for climate DP %s on entity %s",
                        value,
                        dp_name,
                        self._display_name,
                    )
                break

        if changed:
            self._determine_hvac_and_preset_states()

        return changed

    def _determine_hvac_and_preset_states(self) -> None:
        """Determine final HVAC mode, action, and preset mode."""
        # 1. HVAC Action
        if self._is_on_dp_value is False:
            self._attr_hvac_action = HVACAction.OFF
        elif self._heating_active_dp_value:
            self._attr_hvac_action = HVACAction.HEATING
        elif self._cooling_active_dp_value:
            self._attr_hvac_action = HVACAction.COOLING
        else:
            self._attr_hvac_action = HVACAction.IDLE

        # 2. Store last known active HVAC mode
        if self._attr_hvac_action == HVACAction.HEATING:
            self._last_active_hvac_mode = HVACMode.HEAT
        elif self._attr_hvac_action == HVACAction.COOLING:
            self._last_active_hvac_mode = HVACMode.COOL

        # 3. Restore last known active HVAC mode
        if self._is_on_dp_value is False:
            self._attr_hvac_mode = HVACMode.OFF
        elif self._last_active_hvac_mode:
            self._attr_hvac_mode = self._last_active_hvac_mode
        elif HVACMode.HEAT in self.hvac_modes:
            self._attr_hvac_mode = HVACMode.HEAT
        elif HVACMode.COOL in self.hvac_modes:
            self._attr_hvac_mode = HVACMode.COOL

        if self._attr_hvac_mode != HVACMode.OFF and self._current_gira_mode is not None:
            self._attr_preset_mode = GIRA_MODE_TO_HA_PRESET_MAP.get(
                self._current_gira_mode
            )
        else:
            self._attr_preset_mode = None

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature."""
        if (temperature := kwargs.get(ATTR_TEMPERATURE)) is not None:
            _LOGGER.debug(
                "Setting temperature for %s to %s", self._display_name, temperature
            )
            await self._send_command(DP_TARGET_TEMP, temperature)
            # Optimistic update for responsiveness
            self._attr_target_temperature = temperature
            self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if (gira_mode_val := HA_PRESET_TO_GIRA_MODE_MAP.get(preset_mode)) is not None:
            _LOGGER.debug(
                "Setting preset_mode for %s to %s", self._display_name, preset_mode
            )
            await self._send_command(DP_HVAC_MODE, gira_mode_val)
            self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        _LOGGER.debug("Setting hvac_mode for %s to %s", self._display_name, hvac_mode)
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        elif hvac_mode in self.hvac_modes:
            await self.async_turn_on()

    async def async_turn_on(self) -> None:
        """Turn the climate device on."""
        if self._can_write_dp(DP_HVAC_ON_OFF):
            _LOGGER.debug("Turning on climate device %s", self._display_name)
            await self._send_command(DP_HVAC_ON_OFF, 1)
            # Optimistic update for responsiveness
            self._is_on_dp_value = True
            self._determine_hvac_and_preset_states()
            self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn the climate device off."""
        if self._can_write_dp(DP_HVAC_ON_OFF):
            _LOGGER.debug("Turning off climate device %s", self._display_name)
            await self._send_command(DP_HVAC_ON_OFF, 0)
            # Optimistic update for responsiveness
            self._is_on_dp_value = False
            self._determine_hvac_and_preset_states()
            self.async_write_ha_state()
