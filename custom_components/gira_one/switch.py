"""Platform for Gira One Switch entities."""

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GiraApiClient
from .const import (
    DATA_API_CLIENT,
    DATA_LOCATION_MAP,
    DATA_UI_CONFIG,
    DP_ON_OFF,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
    SWITCH,
)
from .entity import GiraOneEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira Switch entities from a config entry."""
    api_client: GiraApiClient = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_API_CLIENT
    ]
    ui_config: dict[str, Any] = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_UI_CONFIG
    ]
    location_map: dict[str, str] = hass.data[config_entry.domain][
        config_entry.entry_id
    ].get(DATA_LOCATION_MAP, {})

    entities = []
    for function_data in ui_config.get("functions", []):
        if (
            GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_data.get("functionType"))
            == SWITCH
        ):
            suggested_area = location_map.get(function_data.get("uid"))
            entities.append(
                GiraSwitch(config_entry, api_client, function_data, suggested_area)
            )
            _LOGGER.info(
                "Adding Gira Switch: %s (UID: %s)",
                function_data.get("displayName"),
                function_data.get("uid"),
            )
    async_add_entities(entities)


class GiraSwitch(GiraOneEntity, SwitchEntity):
    """Representation of a Gira Switch."""

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: dict[str, Any],
        suggested_area: str | None = None,
    ) -> None:
        """Initialize the Gira Switch."""
        super().__init__(config_entry, api_client, function_data, suggested_area)
        self._attr_is_on = None

    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the switch."""
        try:
            data = await self._api.get_value(self.unique_id)
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_dp_value(
                        dp_value_info["uid"], dp_value_info["value"]
                    )
        except Exception as e:
            _LOGGER.exception(
                "Error fetching initial state for switch %s: %s",
                self._display_name,
                e,
            )

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal state. Returns True if state changed."""
        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                if dp_name == DP_ON_OFF:
                    try:
                        is_on = bool(int(value))
                    except (ValueError, TypeError):
                        return False
                    if self._attr_is_on != is_on:
                        self._attr_is_on = is_on
                        return True
                return False
        return False

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the switch on."""
        await self._send_command(DP_ON_OFF, 1)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the switch off."""
        await self._send_command(DP_ON_OFF, 0)
