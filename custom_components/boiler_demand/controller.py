"""Controller that drives the boiler switch from valve positions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from .const import (
    COMMAND_DEBOUNCE_S,
    CONF_FAILSAFE_AFTER,
    CONF_FAILSAFE_MODE,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_OFF_THRESHOLD,
    CONF_ON_THRESHOLD,
    CONF_SWITCH,
    CONF_TEMPERATURES,
    CONF_VALVES,
    DEFAULTS,
    EVAL_INTERVAL,
    signal_update,
)
from .logic import STATUS_DISABLED, STATUS_IDLE, Settings, decide, parse_position

_LOGGER = logging.getLogger(__name__)


def build_settings(options: Mapping[str, Any]) -> Settings:
    """Create logic settings from entry options."""
    return Settings(
        on_threshold=float(options[CONF_ON_THRESHOLD]),
        off_threshold=float(options[CONF_OFF_THRESHOLD]),
        min_on_s=float(options[CONF_MIN_ON]) * 60,
        min_off_s=float(options[CONF_MIN_OFF]) * 60,
        failsafe_after_s=float(options[CONF_FAILSAFE_AFTER]) * 60,
        failsafe_mode=str(options[CONF_FAILSAFE_MODE]),
    )


class BoilerDemandController:
    """Evaluates valve demand and switches the boiler."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.options: dict[str, Any] = {**DEFAULTS, **entry.options}
        self.switch_entity: str = self.options[CONF_SWITCH]
        self.valves: list[str] = list(self.options[CONF_VALVES])
        self.temperatures: dict[str, str | None] = dict(
            self.options.get(CONF_TEMPERATURES) or {}
        )
        self.settings = build_settings(self.options)

        self.device_id: str | None = None  # main device registry id, set on setup
        self.enabled = True
        self.demand = False
        self.status = STATUS_IDLE
        self.remaining_s: float | None = None
        self.positions: dict[str, float | None] = {}
        self.starts_today = 0

        self._offline_since: datetime | None = None
        self._last_command: tuple[str, datetime] | None = None
        self._started = False
        self._unsubs: list[CALLBACK_TYPE] = []

    @property
    def signal(self) -> str:
        """Dispatcher signal for this entry."""
        return signal_update(self.entry.entry_id)

    @property
    def valid_positions(self) -> list[float]:
        """Positions of valves with usable data."""
        return [p for p in self.positions.values() if p is not None]

    async def async_start(self) -> None:
        """Start listening and evaluating."""
        switch_state = self.hass.states.get(self.switch_entity)
        # Seed hysteresis from the relay so a restart does not flip the boiler.
        self.demand = switch_state is not None and switch_state.state == STATE_ON

        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [*self.valves, self.switch_entity], self._on_state_change
            )
        )
        self._unsubs.append(
            async_track_time_interval(self.hass, self._on_interval, EVAL_INTERVAL)
        )
        self._unsubs.append(
            async_track_time_change(
                self.hass, self._on_midnight, hour=0, minute=0, second=0
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._on_started))

    @callback
    def async_stop(self) -> None:
        """Stop all listeners."""
        while self._unsubs:
            self._unsubs.pop()()

    def needs_reload(self, options: Mapping[str, Any]) -> bool:
        """Whether new options change the watched entities."""
        return (
            options.get(CONF_SWITCH) != self.switch_entity
            or list(options.get(CONF_VALVES, [])) != self.valves
            or dict(options.get(CONF_TEMPERATURES) or {}) != self.temperatures
        )

    async def async_apply_options(self, options: Mapping[str, Any]) -> None:
        """Apply tunable options without a reload."""
        self.options = {**DEFAULTS, **options}
        self.settings = build_settings(self.options)
        await self.async_evaluate()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Enable or disable automatic switching."""
        self.enabled = enabled
        await self.async_evaluate()

    async def _on_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        await self.async_evaluate()

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        if event.data["entity_id"] == self.switch_entity:
            old, new = event.data["old_state"], event.data["new_state"]
            if (
                old is not None
                and new is not None
                and old.state == STATE_OFF
                and new.state == STATE_ON
            ):
                self.starts_today += 1
                async_dispatcher_send(self.hass, self.signal)
        self.entry.async_create_task(self.hass, self.async_evaluate())

    async def _on_interval(self, _now: datetime) -> None:
        await self.async_evaluate()

    @callback
    def _on_midnight(self, _now: datetime) -> None:
        self.starts_today = 0
        async_dispatcher_send(self.hass, self.signal)

    async def async_evaluate(self) -> None:
        """Evaluate demand and switch the boiler if needed."""
        now = dt_util.utcnow()
        self.positions = {
            valve: parse_position(state.state if (state := self.hass.states.get(valve)) else None)
            for valve in self.valves
        }

        if self.valid_positions:
            self._offline_since = None
            offline_for_s = None
        else:
            self._offline_since = self._offline_since or now
            offline_for_s = (now - self._offline_since).total_seconds()

        switch_state = self.hass.states.get(self.switch_entity)
        switch_on: bool | None = None
        switch_for_s = 0.0
        if switch_state is not None and switch_state.state in (STATE_ON, STATE_OFF):
            switch_on = switch_state.state == STATE_ON
            switch_for_s = (now - switch_state.last_changed).total_seconds()

        decision = decide(
            list(self.positions.values()),
            self.demand,
            switch_on,
            switch_for_s,
            offline_for_s,
            self.settings,
        )
        self.demand = decision.demand

        if not self.enabled:
            self.status = STATUS_DISABLED
            self.remaining_s = None
        else:
            self.status = decision.status
            self.remaining_s = decision.remaining_s
            if decision.action and self._started:
                await self._async_switch(decision.action, now)

        async_dispatcher_send(self.hass, self.signal)

    async def _async_switch(self, action: str, now: datetime) -> None:
        if self._last_command is not None:
            last_action, last_at = self._last_command
            if last_action == action and (now - last_at).total_seconds() < COMMAND_DEBOUNCE_S:
                return
        self._last_command = (action, now)
        domain = self.switch_entity.split(".", 1)[0]
        _LOGGER.debug("Turning %s %s", self.switch_entity, action)
        try:
            await self.hass.services.async_call(
                domain,
                f"turn_{action}",
                {ATTR_ENTITY_ID: self.switch_entity},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not turn %s %s: %s", self.switch_entity, action, err)
