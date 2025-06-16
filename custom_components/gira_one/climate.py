"""Platform for Gira One Climate entities."""
import logging
from typing import Any, Dict, List, Optional

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
    PRESET_NONE,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, CONF_HOST, UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GiraApiClient
from .const import (
    DOMAIN,
    DATA_API_CLIENT,
    DATA_UI_CONFIG,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    CLIMATE,
    DP_CURRENT_TEMP,
    DP_TARGET_TEMP,
    DP_HVAC_ON_OFF,
    DP_HVAC_MODE,
    DP_HVAC_HEATING_ACTIVE,
    DP_HVAC_COOLING_ACTIVE,
    DP_HVAC_HEAT_COOL_SYSTEM_MODE, # <-- Dieser DP steuert den Betriebsmodus (Heizen/Kühlen)
    GIRA_KNX_HVAC_MODE_COMFORT,
    GIRA_KNX_HVAC_MODE_ECONOMY,
    GIRA_KNX_HVAC_MODE_STANDBY,
    GIRA_KNX_HVAC_MODE_PROTECTION,
    GIRA_KNX_HVAC_MODE_AUTO,
)
from . import SIGNAL_DATA_UPDATE

_LOGGER = logging.getLogger(__name__)

# Mappings für Presets bleiben gleich
GIRA_MODE_TO_HA_PRESET_MAP = {
    GIRA_KNX_HVAC_MODE_COMFORT: PRESET_COMFORT,
    GIRA_KNX_HVAC_MODE_ECONOMY: PRESET_ECO,
    GIRA_KNX_HVAC_MODE_STANDBY: PRESET_AWAY,
    GIRA_KNX_HVAC_MODE_PROTECTION: PRESET_NONE,
}
HA_PRESET_TO_GIRA_MODE_MAP = {v: k for k, v in GIRA_MODE_TO_HA_PRESET_MAP.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    api_client = hass.data[DOMAIN][config_entry.entry_id][DATA_API_CLIENT]
    ui_config = hass.data[DOMAIN][config_entry.entry_id][DATA_UI_CONFIG]
    entities = []
    for function_data in ui_config.get("functions", []):
        if GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_data.get("functionType")) == CLIMATE:
            entities.append(GiraClimate(config_entry, api_client, function_data))
            _LOGGER.info("Adding Gira Climate: %s (UID: %s)", function_data.get("displayName"), function_data.get("uid"))
    async_add_entities(entities)


class GiraClimate(ClimateEntity):
    """Representation of a Gira Climate device (with full preset and operation mode support)."""
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: Dict[str, Any],
    ) -> None:
        """Initialize the Gira Climate device."""
        self._config_entry_id = config_entry.entry_id
        self._api = api_client
        self._function_data = function_data
        self._channel_type = function_data.get("channelType", "")

        self._attr_unique_id = function_data["uid"]
        self._attr_name = function_data.get("displayName", f"Gira Climate {self._attr_unique_id}")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            manufacturer="Gira",
            model=self._channel_type,
            via_device=(DOMAIN, config_entry.unique_id or config_entry.data[CONF_HOST]),
            sw_version=function_data.get("functionType")
        )

        self._data_points: Dict[str, Dict[str, Any]] = { dp["name"]: dp for dp in function_data.get("dataPoints", []) }

        self._attr_current_temperature: Optional[float] = None
        self._attr_target_temperature: Optional[float] = None
        self._attr_hvac_mode: Optional[HVACMode] = None
        self._attr_hvac_action: Optional[HVACAction] = None
        self._attr_preset_mode: Optional[str] = None
        self._attr_preset_modes: List[str] = []

        self._is_on_dp_value: Optional[bool] = None
        self._current_gira_mode: Optional[int] = None
        self._heating_active_dp_value: Optional[bool] = None
        self._cooling_active_dp_value: Optional[bool] = None
        self._system_mode_is_cooling: Optional[bool] = None # <<< NEU: Speichert, ob das System im Kühlmodus ist

        self._update_supported_attributes()

    def _get_dp_uid(self, dp_name: str) -> Optional[str]:
        return self._data_points.get(dp_name, {}).get("uid")

    def _has_dp(self, dp_name: str) -> bool:
        return dp_name in self._data_points

    def _can_write_dp(self, dp_name: str) -> bool:
        return self._data_points.get(dp_name, {}).get("canWrite", False)

    def _update_supported_attributes(self) -> None:
        """Determine supported features, hvac modes, and preset modes."""
        features = ClimateEntityFeature(0)
        hvac_modes: List[HVACMode] = []

        if self._can_write_dp(DP_TARGET_TEMP):
            features |= ClimateEntityFeature.TARGET_TEMPERATURE

        if self._can_write_dp(DP_HVAC_ON_OFF):
            features |= ClimateEntityFeature.TURN_ON | ClimateEntityFeature.TURN_OFF
            hvac_modes.append(HVACMode.OFF)

        if self._can_write_dp(DP_HVAC_MODE):
            features |= ClimateEntityFeature.PRESET_MODE
            self._attr_preset_modes = sorted(list(set(GIRA_MODE_TO_HA_PRESET_MAP.values())))

        elif "RoomTemperatureSwitchable" in self._channel_type: # Fallback für einfache Thermostate
            hvac_modes.append(HVACMode.HEAT)

        self._attr_hvac_modes = sorted(list(set(hvac_modes)))
        self._attr_supported_features = features

    async def async_added_to_hass(self) -> None:
        """Register for callback and fetch initial state."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{SIGNAL_DATA_UPDATE}_{self._config_entry_id}", self._handle_value_update
            )
        )
        try:
            data = await self._api.get_value(self.unique_id)
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_dp_value(dp_value_info["uid"], dp_value_info["value"])
                self._determine_hvac_and_preset_states()
                self.async_write_ha_state()
        except Exception as e:
            _LOGGER.error("Error fetching initial state for climate %s: %s", self.name, e)

    @callback
    def _handle_value_update(self, dp_uid: str, value: Any) -> None:
        if self._update_state_from_dp_value(dp_uid, value):
            self._determine_hvac_and_preset_states()
            self.async_write_ha_state()

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal raw state from a single DP update. Returns True if relevant state changed."""
        changed = False
        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                try:
                    if dp_name == DP_CURRENT_TEMP: self._attr_current_temperature = float(value)
                    elif dp_name == DP_TARGET_TEMP: self._attr_target_temperature = float(value)
                    elif dp_name == DP_HVAC_ON_OFF: self._is_on_dp_value = bool(int(value))
                    elif dp_name == DP_HVAC_MODE: self._current_gira_mode = int(value)
                    elif dp_name == DP_HVAC_HEATING_ACTIVE: self._heating_active_dp_value = bool(int(value))
                    elif dp_name == DP_HVAC_COOLING_ACTIVE:
                        self._cooling_active_dp_value = bool(int(value))
                        # <<< NEU: Zustand des Betriebsmodus speichern (0=Heat, 1=Cool)
                        self._system_mode_is_cooling = bool(int(value))
                    changed = True
                except (ValueError, TypeError): return False
                return changed
        return False

    def _determine_hvac_and_preset_states(self) -> None:
        """Determine final HVAC mode, action, and preset mode based on current DP values."""
        # 1. HVAC Action bestimmen
        if self._is_on_dp_value is False: self._attr_hvac_action = HVACAction.OFF
        elif self._heating_active_dp_value: self._attr_hvac_action = HVACAction.HEATING
        elif self._cooling_active_dp_value: self._attr_hvac_action = HVACAction.COOLING
        else: self._attr_hvac_action = HVACAction.IDLE

        # 2. HVAC Mode bestimmen (<<< GEÄNDERTE LOGIK)
        if self._is_on_dp_value is False:
            self._attr_hvac_mode = HVACMode.OFF
        elif self._system_mode_is_cooling is True: # System ist im Kühlmodus
            self._attr_hvac_mode = HVACMode.COOL
        elif self._system_mode_is_cooling is False: # System ist im Heizmodus
            self._attr_hvac_mode = HVACMode.HEAT
        else: # Fallback, falls der Modus unbekannt, aber das Gerät an ist
            self._attr_hvac_mode = None

        # 3. Preset Mode bestimmen
        if self._attr_hvac_mode != HVACMode.OFF and self._current_gira_mode is not None:
            self._attr_preset_mode = GIRA_MODE_TO_HA_PRESET_MAP.get(self._current_gira_mode, PRESET_NONE)
        else:
            self._attr_preset_mode = None

    async def _send_command(self, dp_name: str, value: Any) -> None:
        dp_uid = self._get_dp_uid(dp_name)
        if dp_uid: await self._api.set_value(dp_uid, value)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            await self._send_command(DP_TARGET_TEMP, temperature)
            self._attr_target_temperature = temperature
            self.async_write_ha_state()

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode."""
        if not self._can_write_dp(DP_HVAC_MODE):
            _LOGGER.warning("Cannot set preset mode, no writable 'Mode' data point for %s", self.name)
            return
        gira_mode_val = HA_PRESET_TO_GIRA_MODE_MAP.get(preset_mode)
        if gira_mode_val is not None:
            if self._is_on_dp_value is False and self._can_write_dp(DP_HVAC_ON_OFF):
                await self._send_command(DP_HVAC_ON_OFF, 1)
            await self._send_command(DP_HVAC_MODE, gira_mode_val)
            self._attr_preset_mode = preset_mode
            self.async_write_ha_state()
        else:
            _LOGGER.warning("Unknown preset mode %s for %s", preset_mode, self.name)

    async def async_turn_on(self) -> None:
        """Turn the climate device on."""
        # Schaltet in den zuletzt bekannten oder Standard-Modus (z.B. Heizen)
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the climate device off."""
        await self.async_set_hvac_mode(HVACMode.OFF)