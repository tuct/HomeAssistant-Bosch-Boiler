"""Controller that drives the boiler switch from valve positions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from functools import partial
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .autotune import HISTORY_DAYS, STUCK_AFTER_S, AutoTuner, replay
from .const import (
    COMMAND_DEBOUNCE_S,
    CONF_FAILSAFE_AFTER,
    CONF_FAILSAFE_MODE,
    CONF_FLOW_SENSOR,
    CONF_MIN_OFF,
    CONF_MIN_ON,
    CONF_OFF_THRESHOLD,
    CONF_ON_THRESHOLD,
    CONF_SWITCH,
    CONF_TEMPERATURES,
    CONF_VALVES,
    DEFAULTS,
    DOMAIN,
    EVAL_INTERVAL,
    FLOW_HOT_C,
    NO_HEAT_AFTER,
    signal_update,
)
from .discovery import valve_name
from .logic import STATUS_DISABLED, STATUS_IDLE, Settings, decide, parse_position

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
SAVE_DELAY_S = 30


def storage_key(entry_id: str) -> str:
    """Store with the learned auto-tune data of one entry."""
    return f"{DOMAIN}.{entry_id}"


def heating_series(
    switch: list[tuple[float, bool]], flow: list[tuple[float, float | None]]
) -> list[tuple[float, bool]]:
    """(time, heat delivered) changes from switch and flow history, like _heat_delivered."""
    events = sorted(
        [(t, 0, on) for t, on in switch] + [(t, 1, temp) for t, temp in flow],
        key=lambda event: event[:2],
    )
    switch_on, temperature = False, None
    out = []
    for t, kind, value in events:
        if kind == 0:
            switch_on = value
        else:
            temperature = value
        out.append((t, switch_on and (temperature is None or temperature >= FLOW_HOT_C)))
    return out


def _number(state: str) -> float | None:
    try:
        return float(state)
    except ValueError:
        return None


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
        self.flow_sensor: str | None = self.options.get(CONF_FLOW_SENSOR) or None
        self.settings = build_settings(self.options)

        self.device_id: str | None = None  # main device registry id, set on setup
        self.enabled = True
        self.demand = False
        self.status = STATUS_IDLE
        self.remaining_s: float | None = None
        self.positions: dict[str, float | None] = {}
        self.starts_today = 0

        self.auto_tune = False
        self.tuner = AutoTuner(
            self.settings.on_threshold, self.settings.off_threshold, self.settings.min_off_s
        )
        self.stuck_valves: set[str] = set()
        self.no_heat = False
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )

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

    def valve_thresholds(self, valve: str) -> tuple[float, float]:
        """(on, off) that currently apply to one valve."""
        if self.auto_tune:
            return self.tuner.thresholds(valve)
        return self.settings.on_threshold, self.settings.off_threshold

    @property
    def flow_temperature(self) -> float | None:
        """Flow pipe temperature in °C, None without a (working) flow sensor."""
        if not self.flow_sensor or (state := self.hass.states.get(self.flow_sensor)) is None:
            return None
        try:
            return float(state.state)
        except ValueError:
            return None

    def _heat_delivered(self, switch_on: bool) -> bool:
        """Boiler switch on and, if a flow sensor reports, the flow pipe hot."""
        flow = self.flow_temperature
        return switch_on and (flow is None or flow >= FLOW_HOT_C)

    @property
    def min_off_s(self) -> float:
        """Minimum off time in effect (auto-tune may extend it)."""
        if self.auto_tune:
            return self.tuner.min_off_s(dt_util.utcnow().timestamp())
        return self.settings.min_off_s

    async def async_start(self) -> None:
        """Start listening and evaluating."""
        stored = await self._store.async_load() or {}
        stored_valves = stored.get("valves") or {}
        self.tuner.load({"valves": {v: d for v, d in stored_valves.items() if v in self.valves}})

        switch_state = self.hass.states.get(self.switch_entity)
        # Seed hysteresis from the relay so a restart does not flip the boiler.
        self.demand = switch_state is not None and switch_state.state == STATE_ON

        self._unsubs.append(
            async_track_state_change_event(
                self.hass,
                [*self.valves, self.switch_entity, *filter(None, self.temperatures.values())],
                self._on_state_change,
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
        for valve in self.stuck_valves:
            ir.async_delete_issue(self.hass, DOMAIN, self._stuck_issue_id(valve))
        self.stuck_valves = set()
        ir.async_delete_issue(self.hass, DOMAIN, self._no_heat_issue_id)
        self.no_heat = False

    def needs_reload(self, options: Mapping[str, Any]) -> bool:
        """Whether new options change the watched entities."""
        return (
            options.get(CONF_SWITCH) != self.switch_entity
            or list(options.get(CONF_VALVES, [])) != self.valves
            or dict(options.get(CONF_TEMPERATURES) or {}) != self.temperatures
            or (options.get(CONF_FLOW_SENSOR) or None) != self.flow_sensor
        )

    async def async_apply_options(self, options: Mapping[str, Any]) -> None:
        """Apply tunable options without a reload."""
        self.options = {**DEFAULTS, **options}
        self.settings = build_settings(self.options)
        self.tuner.set_base(
            self.settings.on_threshold, self.settings.off_threshold, self.settings.min_off_s
        )
        await self.async_evaluate()

    async def async_set_enabled(self, enabled: bool) -> None:
        """Enable or disable automatic switching."""
        self.enabled = enabled
        await self.async_evaluate()

    async def async_set_auto_tune(self, enabled: bool) -> None:
        """Use learned per-valve thresholds, on delay and adaptive minimum off time."""
        self.auto_tune = enabled
        await self.async_evaluate()

    async def async_reset_learning(self) -> None:
        """Forget all learned holding levels."""
        self.tuner.reset(dt_util.utcnow().timestamp())
        await self._store.async_save(self.tuner.as_dict())
        await self.async_evaluate()

    async def async_learn_from_history(self, retrain: bool = False) -> None:
        """Learn holding levels from the recorder's last HISTORY_DAYS days.

        Without retrain only valves that have not learned yet catch up, from history
        after the last reset. With retrain all valves re-learn from history only.
        """
        if "recorder" not in self.hass.config.components:
            return
        from homeassistant.components.recorder import get_instance, history

        valves = [v for v in self.valves if retrain or self.tuner.holding(v) is None]
        if not valves:
            return
        end = dt_util.utcnow()
        start = end - timedelta(days=HISTORY_DAYS)
        if not retrain and self.tuner.since is not None:
            start = max(start, dt_util.utc_from_timestamp(self.tuner.since))
        if start >= end:
            return
        entity_ids = [*valves, self.switch_entity, *filter(None, [self.flow_sensor])]
        states = await get_instance(self.hass).async_add_executor_job(
            partial(
                history.get_significant_states,
                self.hass,
                start,
                end,
                entity_ids,
                include_start_time_state=True,
                no_attributes=True,
            )
        )

        def changes(entity_id: str, parse):
            return [(s.last_changed.timestamp(), parse(s.state)) for s in states.get(entity_id, [])]

        positions = {valve: changes(valve, parse_position) for valve in valves}
        heating = heating_series(
            changes(self.switch_entity, lambda state: state == STATE_ON),
            changes(self.flow_sensor, _number) if self.flow_sensor else [],
        )
        fresh = AutoTuner(
            self.settings.on_threshold, self.settings.off_threshold, self.settings.min_off_s
        )
        await self.hass.async_add_executor_job(
            replay,
            fresh,
            start.timestamp(),
            end.timestamp(),
            EVAL_INTERVAL.total_seconds(),
            positions,
            heating,
        )
        taken = self.tuner.adopt(fresh, replace=retrain)
        _LOGGER.debug("Learned from %s of history: %s", end - start, taken)
        await self._store.async_save(self.tuner.as_dict())
        await self.async_evaluate()

    async def _on_started(self, _hass: HomeAssistant) -> None:
        self._started = True
        await self.async_evaluate()
        self.entry.async_create_background_task(
            self.hass, self.async_learn_from_history(), f"{DOMAIN} learn from history"
        )

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
                self.tuner.record_start(new.last_changed.timestamp())
                async_dispatcher_send(self.hass, self.signal)
        self.entry.async_create_task(self.hass, self.async_evaluate())

    async def _on_interval(self, _now: datetime) -> None:
        await self.async_evaluate()
        # Learning runs on the fixed interval (also with auto-tune off, so it is ready).
        # It only counts while heat really arrives: switch on and, if known, flow pipe hot.
        now = dt_util.utcnow()
        switch_state = self.hass.states.get(self.switch_entity)
        switch_on = switch_state is not None and switch_state.state == STATE_ON
        heating = self._heat_delivered(switch_on)
        changed = self.tuner.observe(now.timestamp(), self.positions, heating)
        if changed:
            self._store.async_delay_save(self.tuner.as_dict, SAVE_DELAY_S)
        stuck = self.tuner.stuck_valves(now.timestamp(), self.positions, heating)
        changed |= stuck != self.stuck_valves
        self._update_stuck_issues(stuck)
        no_heat = (
            switch_on
            and not heating
            and now - switch_state.last_changed >= NO_HEAT_AFTER
        )
        changed |= no_heat != self.no_heat
        self._update_no_heat_issue(no_heat)
        if changed:
            async_dispatcher_send(self.hass, self.signal)

    @property
    def _no_heat_issue_id(self) -> str:
        return f"no_heat_{self.entry.entry_id}"

    @callback
    def _update_no_heat_issue(self, no_heat: bool) -> None:
        """Repairs issue while the switch is on but the flow pipe stays cold."""
        if no_heat and not self.no_heat:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._no_heat_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="no_heat",
                translation_placeholders={
                    "name": self.entry.title,
                    "flow_sensor": str(self.flow_sensor),
                    "minutes": f"{NO_HEAT_AFTER.total_seconds() / 60:g}",
                    "temperature": f"{FLOW_HOT_C:g}",
                },
            )
        elif self.no_heat and not no_heat:
            ir.async_delete_issue(self.hass, DOMAIN, self._no_heat_issue_id)
        self.no_heat = no_heat

    def _stuck_issue_id(self, valve: str) -> str:
        return f"valve_stuck_open_{self.entry.entry_id}_{valve}"

    @callback
    def _update_stuck_issues(self, stuck: set[str]) -> None:
        """Repairs issue per valve that stays fully open although the boiler heats."""
        for valve in stuck - self.stuck_valves:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._stuck_issue_id(valve),
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="valve_stuck_open",
                translation_placeholders={
                    "valve": valve_name(self.hass, valve),
                    "entity_id": valve,
                    "hours": f"{STUCK_AFTER_S / 3600:g}",
                },
            )
        for valve in self.stuck_valves - stuck:
            ir.async_delete_issue(self.hass, DOMAIN, self._stuck_issue_id(valve))
        self.stuck_valves = stuck

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

        settings, thresholds = self.settings, None
        if self.auto_tune:
            thresholds = self.tuner.control_thresholds(now.timestamp(), self.positions)
            settings = replace(settings, min_off_s=self.tuner.min_off_s(now.timestamp()))

        decision = decide(
            list(self.positions.values()),
            self.demand,
            switch_on,
            switch_for_s,
            offline_for_s,
            settings,
            thresholds,
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
