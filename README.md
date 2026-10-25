# Boiler Demand

Home Assistant integration that switches an on/off boiler (Gastherme) from how far your
radiator valves are open. Built for Bosch Smart Home TRVs (`bosch_shc`), works with any
sensor that reports valve opening in % (ZHA, Zigbee2MQTT, ...).

- **Auto-detects valves** – Bosch `…_valvetappet` sensors and common Zigbee valve sensors are pre-selected.
- **Any boiler switch** – `switch` or `input_boolean`.
- **Hysteresis** – on when any valve ≥ on threshold, off when all valves ≤ off threshold, no change in between.
- **Minimum on / off times** – prevents short cycling.
- **Offline-safe** – unavailable, unknown and negative (`-1`) values are ignored; if *all* valves are
  offline for a while, a failsafe turns the boiler on (or off / holds, configurable).
- **Rooms** – each valve becomes a room device with its valve opening and (optional) room temperature;
  the temperature sensor on the same TRV device (or the only one in its area) is found automatically.
- **Dashboard controls** – thresholds, times, failsafe and an automatic-control switch are entities.
- **Auto-tune** (optional) – learns per radiator at which opening its room stays warm and sets the
  thresholds per valve; adds an on delay and stretches the minimum off time when the boiler starts
  too often. Warns in *Repairs* about valves stuck open and (with a flow pipe sensor) a boiler that
  does not heat.

## Install

HACS → ⋮ → Custom repositories → add `https://github.com/tuct/HomeAssistant-Bosch-Boiler`
(type *Integration*), install *Boiler Demand*, restart Home Assistant.

Manual: copy `custom_components/boiler_demand` to `/config/custom_components/` and restart.

Then: Settings → Devices & services → Add integration → *Boiler Demand*.

1. Name and boiler switch.
2. Valve sensors (detected ones are pre-selected).
3. Rooms: a temperature sensor per valve (optional, detected ones are pre-filled).
4. Flow temperature sensor (optional, pre-filled if the relay's device has one), thresholds,
   minimum times and failsafe.

Everything can be changed later under *Configure*. Disable any old automations that switch the boiler.

## Entities

| Entity | Purpose |
| --- | --- |
| `binary_sensor.<name>_heat_demand` | Demand after hysteresis. Attributes: per-valve positions, open and offline valves |
| `sensor.<name>_status` | idle, heating, waiting for min on/off time, valves offline, failsafe, switch unavailable, disabled (+ remaining minutes) |
| `sensor.<name>_max_valve_opening` | Highest valve opening in % |
| `sensor.<name>_boiler_starts_today` | Burner starts since midnight – watch for short cycling |
| `switch.<name>_automatic_control` | Off = compute demand only, never touch the boiler switch |
| `number.<name>_on_threshold` / `_off_threshold` | Hysteresis band in % |
| `number.<name>_minimum_on_time` / `_minimum_off_time` | Anti short-cycling times |
| `select.<name>_failsafe_action` | What to do when all valves are offline |
| `switch.<name>_auto_tune` | Use the learned per-valve thresholds, on delay and adaptive minimum off time (off by default; learning runs anyway) |
| `button.<name>_reset_auto_tune_learning` | Forget everything learned, e.g. after changing radiators or valves |
| `sensor.<name>_effective_minimum_off_time` | Minimum off time in effect, including what auto-tune added |

### Rooms

Every selected valve gets its own **room device** (named after the TRV, e.g. *Heizung Tobi*), linked
under the Boiler Demand device and placed in the valve's area:

| Entity | Purpose |
| --- | --- |
| `sensor.<room>_valve_opening` | Opening in % as used by the controller (unknown = offline/ignored). Attributes: `source_entity`, `role` (demand / holding / closed / offline) |
| `sensor.<room>_room_temperature` | Room temperature from the assigned sensor (only if one is set) |
| `sensor.<room>_holding_level` | Learned holding level in % (unknown while learning). Attributes: `samples`, `on_threshold` / `off_threshold` for this valve, `stuck_open` |

Home Assistant may put the area in front of these entity ids (e.g. `sensor.kuche_heizung_kuche_valve_opening`).

## Dashboard card

The integration ships its own card – no extra HACS frontend repo or dashboard resource needed.
Edit a dashboard → *Add card* → **Boiler Demand** (or search "Boiler").

```yaml
type: custom:boiler-demand-card
device_id: <Boiler Demand device>  # optional, defaults to the first one
title: Heizung                     # optional
show_controls: true                # automatic switch + threshold chips
```

It shows the boiler state (flame), the controller status (waiting / failsafe / …), demand, max valve
opening and starts today, plus every room with temperature and a valve bar marked with the off/on
thresholds. Rooms are found automatically; tap anything for its details, tap *Automatic* to toggle.
After an update, reload the browser page once if the old card is still shown.

Built-in cards work too:

```yaml
type: entities
title: Heizung
entities:
  - entity: switch.heating_automatic_control
  - entity: sensor.heating_status
  - entity: binary_sensor.heating_heat_demand
  - entity: sensor.heating_max_valve_opening
  - entity: sensor.heating_boiler_starts_today
  - type: section
  - entity: number.heating_on_threshold
  - entity: number.heating_off_threshold
  - entity: number.heating_minimum_on_time
  - entity: number.heating_minimum_off_time
  - entity: select.heating_failsafe_action
```

One tile row per room (valve + temperature together):

```yaml
type: grid
columns: 2
square: false
cards:
  - type: tile
    entity: sensor.heizung_tobi_room_temperature
    name: Tobi
  - type: tile
    entity: sensor.heizung_tobi_valve_opening
    name: Ventil
  - type: tile
    entity: sensor.heizung_timon_room_temperature
    name: Timon
  - type: tile
    entity: sensor.heizung_timon_valve_opening
    name: Ventil
```

## Tuning

- Start with on 25 %, off 12 %, min on 10 min, min off 8 min.
- Off threshold must sit above the valves' idle "maintenance" opening (often 5–15 %), otherwise
  the boiler never stops.
- More than ~3–4 starts per hour in mild weather: lower the boiler's flow temperature first, then
  widen the band.

## Auto-tune

A thermostatic valve that has reached its setpoint does not close completely while the boiler
runs: it settles at a steady opening that just replaces the heat the room loses – its **holding
level**. That level differs per radiator (a big radiator holds at 15 %, a small one at 50 %), so one
threshold for all valves either starts the boiler for rooms that are already warm, or waits too long
for rooms that are cold.

**Learning** (always running, also with the switch off, so it is ready when you turn it on):

1. Every 30 s each valve's opening is recorded – only while heat really arrives: boiler switch on
   and, with a flow sensor, the flow pipe at least 30 °C.
2. If a valve stayed within ±3 % for 30 minutes, between 3 % and 90 % (not closed, not wide open
   because the room is cold), that average is one sample. At most one sample per 10 minutes.
3. The holding level is a moving average of the samples (each new one counts 20 %), so it follows
   the season slowly. It is used after 3 samples and stored across restarts.

**Control with `switch.<name>_auto_tune` on**:

| | Rule | Example: holding 30 % |
| --- | --- | --- |
| Off threshold per valve | holding + 3 %, max 60 % | 33 % – valve is back at "warm enough" |
| On threshold per valve | off + 7 % | 40 % – room is clearly asking for more |
| Floor | never below the configured on / off thresholds | |
| On delay | a valve must stay above its on threshold for 3 min before it may start the boiler (it keeps a running boiler on immediately) | filters short spikes after a window was opened |
| Minimum off time | more than 3 starts within an hour: +5 min (up to 20 min); every start that drops out of the hour: −1 min; an hour without starts: back to the configured value | |

Valves that have not learned yet use the configured thresholds. Roles (*demand / holding / closed*) on
the card, the ESPHome display and the `rooms` attribute follow the per-valve thresholds.

**Repairs warnings** (independent of the switch):

- *Valve stays fully open* – ≥ 95 % for 4 hours while heat arrives: the valve is stuck, or no heat
  reaches that radiator (air, closed lock shield). Such a valve keeps the boiler running.
- *Boiler is not heating* (flow sensor only) – switch on for 30 minutes but the flow pipe is still
  below 30 °C: boiler off, in summer / hot-water-only mode, or in fault.

Both clear themselves once the condition is gone. With the boiler off, valves open fully while they
wait for heat – that is normal and not counted.

**Try it on your own history first**: Settings → History → pick the valve sensors → download, then

```
python tools/simulate.py history.csv
python tools/simulate.py history.csv --from "2026-10-20 13:00" --on 25 --off 20
```

It replays the export through the old automations, the configured thresholds and auto-tune (the
integration's own code) and prints on time, starts and the learned holding levels. Use `--from` /
`--to` to cut out days without heat. It is open loop – the valves reacted to the boiler that really
ran – so use it to compare strategies, not as an exact forecast.

## Hardware (ESPHome boiler relay)

Minimum:

| Part | Notes |
| --- | --- |
| ESP32 board, e.g. Seeed XIAO ESP32-C3 | Any ESP32 with Wi-Fi. Config: `esphome/boiler-relay-c3.yaml` |
| 3.3 V relay module, e.g. Relay-T73 V2.1 (SRD-03VDC) | VCC → 3V3, GND → GND, IN → GPIO. Contacts **COM + NC** on the boiler's room-thermostat terminals, pin `inverted: true` |
| *Only for a bare SRD-03VDC:* NPN (2N2222 / BC337) + 1 kΩ + 1N4148 | The ~120 mA coil is too much for a GPIO; a module already has this driver |
| USB power supply 5 V | |
| *With a display:* MP1584 buck converter, set to **3.3 V** | Separate 3.3 V rail for relay module, TFT (VCC and BL) and rotary encoder. From the XIAO's own 3V3 the backlight dims while the relay coil (~100 mA) is on. Input from the XIAO's 5V pin (USB-C VBUS) – one USB supply (≥ 1 A) for everything. Set the output with a multimeter **before** connecting anything – new modules often ship at a higher voltage |

Why COM + NC: the contact is closed while the relay coil is unpowered, so a dead ESP, a power cut
or an unflashed board means *boiler on* — the TRVs keep regulating the rooms. The pin is inverted so
the ESPHome switch still reads naturally (ON = boiler on = coil off). The coil draws ~100 mA while
the boiler is off. Verify with a multimeter on COM/NC: switch ON must read closed; if not, the module
is low-level triggered (H/L jumper) → use `inverted: false`.

Optional:

| Part | Notes |
| --- | --- |
| 1.8" TFT, ST7735, 128×160, SPI | LVGL UI. Status page: boiler state, Boiler Demand status, max valve opening, average temperature, rooms (count / calling / offline), HA connection / FAILSAFE, time. Room page: one room at a time (temperature, valve bar, role) – the room list comes from HA at runtime, no reflash when rooms change. Menu: Automatik on/off. Package `esphome/boiler-tft-st7735.yaml`, complete node `esphome/boiler-relay-c3-tft.yaml` (wiring in the file) |
| Rotary encoder with button (KY-040 / EC11), on top of the TFT | Turn to flip Status → rooms, press for the Automatik menu. Package `esphome/boiler-rotary.yaml`, complete node `esphome/boiler-relay-c3-tft-rotary.yaml`. Needs the ESPHome device option *Allow the device to perform Home Assistant actions* |
| DS18B20 waterproof probe + 4.7 kΩ resistor | Flow pipe (Vorlauf) temperature, shows whether the boiler really delivers heat. DQ → D7 (GPIO20), 4.7 kΩ pull-up DQ → 3V3. Strap it to the flow pipe right after the boiler with thermal paste and insulate it. Package `esphome/boiler-flow-temp.yaml`, included in `boiler-relay-c3.yaml` (remove the line if not fitted) |
| OLED (SH1106 / SSD1306), touch screen | Local status and manual override, see `esphome/mista-heatcontrol.yaml` |

The ESPHome files are layered packages – pick the node that matches your hardware:

| Node | Layers |
| --- | --- |
| `boiler-relay-c3.yaml` | base: relay + watchdog + flow temperature (DS18B20) |
| `boiler-relay-c3-tft.yaml` | base → TFT status screen |
| `boiler-relay-c3-tft-rotary.yaml` | base → TFT → rotary menu |

Rotary controls: turn flips Status → room 1 → … → room N → Status; press opens the menu with the
*Automatik* switch (set `allow_automatic_toggle: "false"` in the TFT package to disable it). In the
menu, turn moves between *Automatic* and *OK*; 30 s idle (`home_timeout`) returns to the status page. If Home
Assistant does not allow the device's actions, the switch flips back to the real state. The display
is English by default; `language: de` in the TFT package vars switches it to German. The backlight
(BL/BLK) is wired to 3V3 – PWM dimming flickers – so the screen stays on.

The room list is the `rooms` attribute of `sensor.<name>_status` – a compact JSON list
(`n` name, `t` temperature, `v` valve %, `r` role: d demand / h holding / c closed / o offline) that any
display can use.

Safety: check the boiler manual for the voltage on the thermostat terminals (24 V or 230 V). For 230 V,
use a relay module rated for mains with proper isolation and an enclosure; never put mains on the ESP
side.

## Wiring

Pins as used in the ESPHome files (Seeed XIAO ESP32-C3).

### Minimum: XIAO + relay (`boiler-relay-c3.yaml`)

```
 USB 5 V ──► XIAO ESP32-C3 (USB-C)

             XIAO ESP32-C3                     Relay module (Relay-T73 V2.1, 3.3 V)
          ┌─────────────────┐                ┌──────────────────────────┐
          │ 3V3 ────────────┼───────────────►│ VCC                      │
          │ GND ────────────┼───────────────►│ GND                      │
          │ D2  (GPIO4) ────┼───────────────►│ IN                       │
          └─────────────────┘                │ COM ──────────────┐      │
                                             │ NC  ────────────┐ │      │
                                             │ NO  (unused)    │ │      │
                                             └─────────────────┼─┼──────┘
                                                               │ │
                                              Boiler room-thermostat terminals
                                              (RT / 3-4 / …, potential-free contact)
```

COM + NC: relay coil off = contact closed = boiler on, so a dead ESP or power cut fails to heating.

Optional flow pipe sensor (DS18B20): VDD → 3V3, GND → GND, DQ → D7 (GPIO20), 4.7 kΩ between DQ and
3V3 — same as in the diagram below.

> **Check your boiler first: does it heat when the thermostat terminals are *bridged* (closed) or
> *open*?** Test it before wiring – briefly bridge the terminals (or disconnect the existing thermostat)
> and watch the burner.
>
> | Boiler heats when terminals are … | Relay contacts | ESPHome |
> | --- | --- | --- |
> | **closed** (most boilers) | **COM + NC** | `inverted: true` |
> | **open** | **COM + NO** | `inverted: true` (unchanged) |
>
> Both keep the failsafe: with the relay coil unpowered (dead ESP, power cut) the contact is in the
> state that makes the boiler heat, and the ESPHome switch *Boiler* ON still means coil off.

### With display + rotary + MP1584 (`boiler-relay-c3-tft-rotary.yaml`)

*3V3 rail* = MP1584 OUT+. *GND* = common ground (XIAO GND, MP1584 IN-/OUT-, all modules).

```
 USB-C 5 V ──► XIAO ESP32-C3 (USB-C)

             XIAO ESP32-C3                      MP1584 buck converter (set to 3.3 V!)
          ┌─────────────────┐                ┌─────────────────┐
          │ 5V (VBUS)    ───┼───────────────►│ IN+             │
          │                 │                │ IN-  ◄── GND    │
          │                 │                │ OUT+ ───────────┼──► 3V3 rail
          │                 │                │ OUT- ◄── GND    │
          │                 │                └─────────────────┘
          │                 │                   1.8" TFT ST7735 (128×160, SPI)
          │                 │                ┌─────────────────┐
          │ D8  (GPIO8)  ───┼───────────────►│ SCK / SCL       │
          │ D10 (GPIO10) ───┼───────────────►│ SDA / MOSI      │
          │ D1  (GPIO3)  ───┼───────────────►│ CS              │
          │ D3  (GPIO5)  ───┼───────────────►│ DC / A0         │
          │ D6  (GPIO21) ───┼───────────────►│ RES / RST       │
          │                 │                │ VCC  ◄── 3V3 rail
          │                 │                │ BL   ◄── 3V3 rail
          │                 │                │ GND  ◄── GND    │
          │                 │                └─────────────────┘
          │                 │                   Rotary encoder (KY-040 / EC11)
          │                 │                ┌─────────────────┐
          │ D4  (GPIO6)  ◄──┼────────────────┤ CLK / A         │
          │ D5  (GPIO7)  ◄──┼────────────────┤ DT  / B         │
          │ D0  (GPIO2)  ◄──┼────────────────┤ SW              │
          │                 │                │ +    ◄── 3V3 rail
          │                 │                │ GND  ◄── GND    │
          │                 │                └─────────────────┘
          │                 │                   Relay module (3.3 V)
          │                 │                ┌─────────────────┐
          │ D2  (GPIO4)  ───┼───────────────►│ IN              │
          │                 │                │ VCC  ◄── 3V3 rail
          │                 │                │ GND  ◄── GND    │
          │                 │                │ COM + NC ───────┼──► Boiler RT terminals
          │                 │                └─────────────────┘
          │                 │                   DS18B20 probe (flow pipe)
          │                 │                ┌─────────────────┐
          │ D7  (GPIO20) ◄──┼──────┬─────────┤ DQ  (yellow)    │
          │                 │    4.7 kΩ      │ VDD ◄── 3V3 rail│
          │                 │      └─► 3V3   │ GND ◄── GND     │
          └─────────────────┘                └─────────────────┘
```

- Set the MP1584 output to 3.3 V with a multimeter **before** connecting anything.
- Relay contacts: **COM + NC** if the boiler heats on closed terminals, **COM + NO** if it heats on
  open terminals – see the check above.
- One USB-C supply (5 V, ≥ 1 A) powers everything: the MP1584 takes its input from the XIAO's 5V
  (VBUS) pin.
- Relay, TFT (incl. backlight) and encoder all run on the MP1584 3.3 V rail, so the backlight does
  not dim when the relay switches; the XIAO's own 3V3 pin stays unused.
- DS18B20: the 4.7 kΩ pull-up between DQ and the 3V3 rail is required. GPIO20 is UART0 RX, free
  because the logger uses the USB serial.
- GPIO2 (encoder button) and GPIO8 (TFT clock) are strapping pins: fine here, just don't hold the
  button while powering up.
- Boiler terminals: check the voltage in the boiler manual (24 V or 230 V) and use a relay rated for it.

## ESPHome relay watchdog

`esphome/boiler-watchdog.yaml` is a package for the ESPHome node that drives the boiler relay. If Home
Assistant has not been connected for 10 minutes, it turns the relay **on** — the TRVs keep regulating
locally, so the flat stays warm while HA is down. Pair it with `restore_mode: RESTORE_DEFAULT_ON` on
the relay. See `esphome/mista-heatcontrol.yaml` for a complete node.

## Development

### Try the UI locally

`dev/` runs a throw-away Home Assistant in Docker with this integration mounted and simulated rooms
(valve and temperature sliders, a fake boiler switch):

```
docker compose -f dev/docker-compose.yml up -d
```

Open http://localhost:8123, create a user, then add *Boiler Demand* (boiler: *Therme (simulated relay)*).
Move the sliders on the Overview dashboard; `-1` simulates a valve that is not ready. After changing
the code: `docker compose -f dev/docker-compose.yml restart`. The simulated sensors have no device, so
assign them to areas (or pick temperatures by hand in the Rooms step) to try temperature detection.

### Tests

```
pip install pytest pytest-homeassistant-custom-component
pytest
```

`tests/test_logic.py` and `tests/test_autotune.py` cover the pure decision and auto-tune logic;
`tests/test_integration.py` runs the config flow and controller against Home Assistant core.
