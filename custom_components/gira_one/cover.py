"""Platform for Gira One Cover entities."""

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    ATTR_TILT_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import GiraApiClient
from .const import (
    COVER,
    DATA_API_CLIENT,
    DATA_UI_CONFIG,
    DP_MOVEMENT,
    DP_POSITION,
    DP_SLAT_POSITION,
    DP_STEP_UP_DOWN,
    DP_UP_DOWN,
    GIRA_FUNCTION_TYPE_TO_HA_PLATFORM,
)
from .entity import GiraOneEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Gira Cover entities from a config entry."""
    api_client: GiraApiClient = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_API_CLIENT
    ]
    ui_config: dict[str, Any] = hass.data[config_entry.domain][config_entry.entry_id][
        DATA_UI_CONFIG
    ]

    entities = []
    for function_data in ui_config.get("functions", []):
        if (
            GIRA_FUNCTION_TYPE_TO_HA_PLATFORM.get(function_data.get("functionType"))
            == COVER
        ):
            entities.append(GiraCover(config_entry, api_client, function_data))
            _LOGGER.info(
                "Adding Gira Cover: %s (UID: %s)",
                function_data.get("displayName"),
                function_data.get("uid"),
            )
    async_add_entities(entities)


class GiraCover(GiraOneEntity, CoverEntity):
    """Representation of a Gira Cover."""

    # Activate optimistic mode
    _attr_assumed_state = True

    def __init__(
        self,
        config_entry: ConfigEntry,
        api_client: GiraApiClient,
        function_data: dict[str, Any],
    ) -> None:
        """Initialize the Gira Cover."""
        super().__init__(config_entry, api_client, function_data)

        self._attr_current_cover_position: int | None = None
        self._attr_current_cover_tilt_position: int | None = None
        self._attr_is_moving: bool = False
        self._attr_is_opening: bool = False
        self._attr_is_closing: bool = False

        if self._can_write_dp(DP_SLAT_POSITION):
            self._attr_device_class = CoverDeviceClass.BLIND
        else:
            self._attr_device_class = CoverDeviceClass.SHUTTER

        self._update_supported_features()

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed or not."""
        # Home Assistant standard: 0 is closed.
        if self.current_cover_position is None:
            return None
        return self.current_cover_position == 0

    def _update_supported_features(self) -> None:
        """Determine supported features based on all available data points."""
        features = CoverEntityFeature(0)
        if self._can_write_dp(DP_POSITION) or self._has_dp(DP_UP_DOWN):
            features |= CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if self._can_write_dp(DP_POSITION):
            features |= CoverEntityFeature.SET_POSITION
        if self._has_dp(DP_STEP_UP_DOWN):
            features |= CoverEntityFeature.STOP
        if self._can_write_dp(DP_SLAT_POSITION):
            features |= (
                CoverEntityFeature.OPEN_TILT
                | CoverEntityFeature.CLOSE_TILT
                | CoverEntityFeature.SET_TILT_POSITION
            )
            if features & CoverEntityFeature.STOP:
                features |= CoverEntityFeature.STOP_TILT
        self._attr_supported_features = features

    async def _fetch_initial_state(self) -> None:
        """Fetch the initial state of the cover."""
        try:
            data = await self._api.get_value(self.unique_id)
            if data and "values" in data:
                for dp_value_info in data["values"]:
                    self._update_state_from_dp_value(
                        dp_value_info["uid"], dp_value_info["value"]
                    )
        except Exception as e:
            _LOGGER.exception("Error fetching initial state for cover %s: %s", self.name, e)

    def _update_state_from_dp_value(self, dp_uid_updated: str, value: Any) -> bool:
        """Update internal state. Returns True if state changed."""
        changed = False
        for dp_name, dp_info in self._data_points.items():
            if dp_info["uid"] == dp_uid_updated:
                try:
                    if dp_name == DP_POSITION:
                        # Invert: Gira (0=open, 100=closed) to HA (100=open, 0=closed)
                        gira_position = int(float(value))
                        ha_position = 100 - gira_position
                        if self._attr_current_cover_position != ha_position:
                            self._attr_current_cover_position = ha_position
                            changed = True
                    elif dp_name == DP_SLAT_POSITION:
                        # Invert: Gira (0=open, 100=closed) to HA (100=open, 0=closed)
                        gira_tilt_position = int(float(value))
                        ha_tilt_position = 100 - gira_tilt_position
                        if self._attr_current_cover_tilt_position != ha_tilt_position:
                            self._attr_current_cover_tilt_position = ha_tilt_position
                            changed = True
                    elif dp_name == DP_MOVEMENT:
                        is_moving_now = bool(int(value))
                        if self._attr_is_moving != is_moving_now:
                            self._attr_is_moving = is_moving_now
                            changed = True
                            # Reset if moving is done
                            if not is_moving_now:
                                self._attr_is_opening = False
                                self._attr_is_closing = False

                except (ValueError, TypeError):
                    _LOGGER.warning(
                        "Could not parse value '%s' for cover DP %s on entity %s",
                        value,
                        dp_name,
                        self.name,
                    )
                break
        return changed

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the cover."""
        _LOGGER.debug("Opening cover %s", self.name)
        if self._has_dp(DP_UP_DOWN):
            await self._send_command(DP_UP_DOWN, 0)
        else:
            # Home Assistant standard: 100 is open.
            await self.async_set_cover_position(position=100)

        # Optimistic update
        self._attr_is_moving = True
        self._attr_is_opening = True
        self._attr_is_closing = False
        self.async_write_ha_state()

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the cover."""
        _LOGGER.debug("Closing cover %s", self.name)
        if self._has_dp(DP_UP_DOWN):
            await self._send_command(DP_UP_DOWN, 1)
        else:
            # Home Assistant standard: 0 is closed.
            await self.async_set_cover_position(position=0)

        # Optimistic update
        self._attr_is_moving = True
        self._attr_is_opening = False
        self._attr_is_closing = True
        self.async_write_ha_state()

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the cover."""
        if self._has_dp(DP_STEP_UP_DOWN):
            _LOGGER.debug("Stopping cover %s", self.name)
            await self._send_command(DP_STEP_UP_DOWN, 1)

        # Optimistic update
        self._attr_is_moving = False
        self._attr_is_opening = False
        self._attr_is_closing = False
        self.async_write_ha_state()

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Set the cover position."""
        ha_position = kwargs[ATTR_POSITION]
        # Invert: HA (100=open, 0=closed) to Gira (0=open, 100=closed)
        gira_position = 100 - ha_position
        _LOGGER.debug("Setting cover %s to HA position %s (Gira: %s)", self.name, ha_position, gira_position)
        await self._send_command(DP_POSITION, gira_position)

        # Optimistic update
        self._attr_current_cover_position = ha_position
        self._attr_is_moving = True
        self.async_write_ha_state()

    async def async_open_cover_tilt(self, **kwargs: Any) -> None:
        """Open the cover tilt."""
        # Home Assistant standard: 100 is open.
        await self.async_set_cover_tilt_position(tilt_position=100)

    async def async_close_cover_tilt(self, **kwargs: Any) -> None:
        """Close the cover tilt."""
        # Home Assistant standard: 0 is closed.
        await self.async_set_cover_tilt_position(tilt_position=0)

    async def async_stop_cover_tilt(self, **kwargs: Any) -> None:
        """Stop the cover tilt."""
        if self._has_dp(DP_STEP_UP_DOWN):
            _LOGGER.debug("Stopping cover tilt on %s", self.name)
            await self._send_command(DP_STEP_UP_DOWN, 1)

        # Optimistic update
        self._attr_is_moving = False
        self.async_write_ha_state()

    async def async_set_cover_tilt_position(self, **kwargs: Any) -> None:
        """Set the cover tilt position."""
        # Invert: HA (100=open, 0=closed) to Gira (0=open, 100=closed)
        ha_tilt_position = kwargs[ATTR_TILT_POSITION]
        gira_tilt_position = 100 - ha_tilt_position
        _LOGGER.debug("Setting cover tilt %s to HA position %s (Gira: %s)", self.name, ha_tilt_position, gira_tilt_position)
        await self._send_command(DP_SLAT_POSITION, gira_tilt_position)

        # Optimistic update
        self._attr_current_cover_tilt_position = ha_tilt_position
        self._attr_is_moving = True
        self.async_write_ha_state()