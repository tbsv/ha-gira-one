"""Platform for Gira One sensor entities."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import SIGNAL_DATA_UPDATE
from .api import GiraApiClient, GiraApiClientError
from .const import (
    CLIMATE,
    DATA_API_CLIENT,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DOMAIN,
    DP_CURRENT_TEMP,
    DP_TARGET_TEMP,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
)

_LOGGER = logging.getLogger(__name__)

# Maps data point name to (translation_key, unique_id_suffix)
_TEMP_SENSOR_TYPES: dict[str, tuple[str, str]] = {
    DP_CURRENT_TEMP: ("current_temperature", "current_temperature"),
    DP_TARGET_TEMP: ("target_temperature", "target_temperature"),
}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira One sensor entities from a config entry."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    api_client: GiraApiClient = entry_data[DATA_API_CLIENT]
    ui_config: dict[str, Any] = entry_data[DATA_UI_CONFIG]
    location_map: dict[str, str] = entry_data.get(DATA_LOCATION_MAP, {})

    entities: list[SensorEntity] = []
    for function_data in ui_config.get("functions", []):
        if (
            GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_data.get("functionType"))
            != CLIMATE
        ):
            continue

        data_points = {dp["name"]: dp for dp in function_data.get("dataPoints", [])}
        suggested_area = location_map.get(function_data.get("uid"))

        for dp_name, (translation_key, uid_suffix) in _TEMP_SENSOR_TYPES.items():
            if dp_name not in data_points:
                continue
            entities.append(
                GiraTemperatureSensor(
                    config_entry=config_entry,
                    api_client=api_client,
                    function_data=function_data,
                    dp_uid=data_points[dp_name]["uid"],
                    translation_key=translation_key,
                    uid_suffix=uid_suffix,
                    suggested_area=suggested_area,
                )
            )
            _LOGGER.info(
                "Adding Gira %s sensor: %s (UID: %s)",
                dp_name,
                function_data.get("displayName"),
                function_data.get("uid"),
            )

    async_add_entities(entities)


class GiraTemperatureSensor(SensorEntity):
    """Temperature sensor exposed alongside a Gira climate function."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: dict[str, Any],
        dp_uid: str,
        translation_key: str,
        uid_suffix: str,
        suggested_area: str | None,
    ) -> None:
        """Initialize the temperature sensor."""
        self._config_entry_id = config_entry.entry_id
        self._api = api_client
        self._function_uid = function_data["uid"]
        self._dp_uid = dp_uid

        self._attr_unique_id = f"{self._function_uid}_{uid_suffix}"
        self._attr_translation_key = translation_key
        self._attr_suggested_area = suggested_area
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._function_uid)},
            name=function_data.get("displayName"),
            manufacturer="Gira",
            model=function_data.get("functionType", "Unknown Gira Function"),
            via_device=(
                DOMAIN,
                config_entry.unique_id or config_entry.data.get("host"),
            ),
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to dispatcher updates and fetch initial state."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_DATA_UPDATE}_{self._config_entry_id}",
                self._handle_value_update,
            )
        )
        await self._fetch_initial_state()

    @callback
    def _handle_value_update(self, dp_uid: str, value: Any) -> None:
        """Handle a dispatched value update for our data point."""
        if dp_uid != self._dp_uid:
            return
        if self._set_value(value):
            self.async_write_ha_state()

    async def _fetch_initial_state(self) -> None:
        """Fetch the initial value of the temperature data point."""
        try:
            data = await self._api.get_value(self._function_uid)
        except GiraApiClientError as err:
            _LOGGER.debug(
                "Error fetching initial state for sensor %s: %s",
                self._attr_unique_id,
                err,
            )
            return

        for dp_value_info in data.get("values", []):
            if dp_value_info.get("uid") == self._dp_uid:
                self._set_value(dp_value_info.get("value"))
                break

    def _set_value(self, value: Any) -> bool:
        """Parse and store the temperature value, returning True if changed."""
        try:
            new_value = float(value)
        except (TypeError, ValueError):
            _LOGGER.warning(
                "Could not parse temperature value '%s' for sensor %s",
                value,
                self._attr_unique_id,
            )
            return False

        if self._attr_native_value == new_value:
            return False
        self._attr_native_value = new_value
        return True
