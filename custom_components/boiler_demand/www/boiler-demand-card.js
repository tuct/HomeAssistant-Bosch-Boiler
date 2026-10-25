/**
 * Boiler Demand card – served and loaded by the boiler_demand integration.
 *
 *   type: custom:boiler-demand-card
 *   device_id: <Boiler Demand device>   # optional, defaults to the first one
 *   title: Heizung                       # optional
 *   show_controls: true                  # optional, automatic switch + thresholds
 *
 *   type: custom:boiler-demand-history-card   # valves, temperatures and boiler over time
 *   device_id: <Boiler Demand device>   # optional
 *   range: 7d                            # optional, 24h | 7d (switchable on the card)
 *
 * Rooms are discovered from the device registry: every "Room" device linked to the
 * Boiler Demand device, with its "valve_opening" and "room_temperature" entities.
 */

const CARD_VERSION = "0.8.0";
const DOMAIN = "boiler_demand";
const MAIN_MODEL = "Valve-based boiler control";
const ROOM_MODEL = "Room";

const STRINGS = {
  en: {
    heating: "Heating",
    off: "Off",
    starts: "starts today",
    max: "max valve",
    auto: "Automatic",
    on_th: "on",
    off_th: "off",
    offline: "offline",
    no_device: "No Boiler Demand device found. Add the integration first.",
    left: "min left",
    device: "Boiler Demand device",
    title: "Title (optional)",
    show_controls: "Show automatic switch and thresholds",
    demand: "Demand",
    no_demand: "No demand",
    rooms: "rooms",
    room: "room",
    calling: "calling",
    avg: "avg",
    auto_tune: "Auto-tune",
    inactive: "inactive",
    th_floor: "minimum – auto-tune can raise it per room",
    last_24h: "last 24 h",
    last_7d: "last 7 days",
    on_for: "on",
    starts_n: "starts",
    per_day: "/day",
    longest: "longest",
    holding: "holding",
    learning: "learning",
    boiler: "Boiler",
    flow: "Flow",
    valve: "valve",
    temperature: "temperature",
    holding_level: "holding level (learned)",
    role_demand: "demand",
    role_holding: "holding",
    role_closed: "closed",
    role_offline: "offline",
    stuck: "fully open while heating – stuck?",
    loading: "Loading history…",
    no_history: "No history – is the recorder enabled?",
    range: "Default range",
  },
  de: {
    heating: "Heizt",
    off: "Aus",
    starts: "Starts heute",
    max: "max. Ventil",
    auto: "Automatik",
    on_th: "ein",
    off_th: "aus",
    offline: "offline",
    no_device: "Kein Boiler-Demand-Gerät gefunden. Zuerst die Integration hinzufügen.",
    left: "Min. übrig",
    device: "Boiler-Demand-Gerät",
    title: "Titel (optional)",
    show_controls: "Automatik-Schalter und Schwellen anzeigen",
    demand: "Anforderung",
    no_demand: "Keine Anforderung",
    rooms: "Räume",
    room: "Raum",
    calling: "heizen",
    avg: "Ø",
    auto_tune: "Auto-Tuning",
    inactive: "inaktiv",
    th_floor: "Untergrenze – Auto-Tuning kann sie pro Raum anheben",
    last_24h: "letzte 24 h",
    last_7d: "letzte 7 Tage",
    on_for: "an",
    starts_n: "Starts",
    per_day: "/Tag",
    longest: "längste",
    holding: "hält bei",
    learning: "lernt",
    boiler: "Therme",
    flow: "Vorlauf",
    valve: "Ventil",
    temperature: "Temperatur",
    holding_level: "Halteöffnung (gelernt)",
    role_demand: "Anforderung",
    role_holding: "hält",
    role_closed: "zu",
    role_offline: "offline",
    stuck: "voll offen trotz Heizen – hängt?",
    loading: "Lade Verlauf…",
    no_history: "Kein Verlauf – ist der Recorder aktiv?",
    range: "Standard-Zeitraum",
  },
};

function tr(hass, key) {
  const lang = (hass?.locale?.language || hass?.language || "en").split("-")[0];
  return (STRINGS[lang] || STRINGS.en)[key] ?? STRINGS.en[key] ?? key;
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function mainDevices(hass) {
  return Object.values(hass?.devices || {})
    .filter((d) => d.model === MAIN_MODEL && (d.identifiers || []).some((i) => i[0] === DOMAIN))
    .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
}

/** translation_key -> entity_id for the entities of one device. */
function deviceEntities(hass, deviceId) {
  const out = {};
  for (const entry of Object.values(hass.entities || {})) {
    if (entry.device_id === deviceId && entry.platform === DOMAIN && entry.translation_key) {
      out[entry.translation_key] = entry.entity_id;
    }
  }
  return out;
}

function deviceName(device) {
  return device.name_by_user || device.name || "";
}

/** Drop leading words all room names share ("Heizung Tobi", "Heizung Timon" -> "Tobi", "Timon"). */
function shortNames(names) {
  if (names.length < 2) return names;
  const split = names.map((n) => n.split(/\s+/));
  let common = 0;
  while (split.every((w) => w.length > common + 1 && w[common] === split[0][common])) common += 1;
  return split.map((w) => w.slice(common).join(" "));
}

function numberState(hass, entityId) {
  const value = parseFloat(hass.states[entityId]?.state);
  return Number.isFinite(value) ? value : null;
}

/** Thresholds that apply to one room: learned ones while auto-tune is on. */
function roomThresholds(hass, room, autoTune, onTh, offTh) {
  const attrs = hass.states[room.entities.holding_level]?.attributes;
  if (autoTune && attrs && Number.isFinite(attrs.on_threshold) && Number.isFinite(attrs.off_threshold)) {
    return { on: attrs.on_threshold, off: attrs.off_threshold };
  }
  return { on: onTh, off: offTh };
}

/** Shared device/entity lookup for both cards. */
class BoilerDemandBase extends HTMLElement {
  static getStubConfig(hass) {
    const device = mainDevices(hass)[0];
    return device ? { device_id: device.id } : {};
  }

  set hass(hass) {
    this._hass = hass;
    this._hassChanged?.();
    const key = this._stateKey();
    if (key !== this._lastKey) {
      this._lastKey = key;
      this._render();
    }
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: "auto" };
  }

  /** Main device, its entities and its rooms – or null. */
  _model() {
    const hass = this._hass;
    if (!hass?.devices || !hass?.entities) return null;
    const devices = mainDevices(hass);
    const main = devices.find((d) => d.id === this._config?.device_id) || devices[0];
    if (!main) return null;
    const rooms = Object.values(hass.devices)
      .filter((d) => d.via_device_id === main.id && d.model === ROOM_MODEL)
      .map((d) => ({ device: d, name: deviceName(d), entities: deviceEntities(hass, d.id) }))
      .sort((a, b) => a.name.localeCompare(b.name));
    shortNames(rooms.map((r) => r.name)).forEach((name, i) => { rooms[i].name = name; });
    return { main, entities: deviceEntities(hass, main.id), rooms };
  }

  /** Re-render only when something shown on the card changed. */
  _stateKey() {
    const model = this._model();
    if (!model) return "none";
    const ids = [
      ...Object.values(model.entities),
      ...model.rooms.flatMap((r) => Object.values(r.entities)),
    ];
    const boiler = this._hass.states[model.entities.status]?.attributes?.boiler_switch;
    if (boiler) ids.push(boiler);
    return ids
      .map((id) => {
        const s = this._hass.states[id];
        return s ? `${id}=${s.state}|${JSON.stringify(s.attributes)}` : id;
      })
      .join(";") + `;${this._hass.locale?.language}`;
  }

  _fmt(entityId) {
    const stateObj = this._hass.states[entityId];
    if (!stateObj) return "–";
    return this._hass.formatEntityState ? this._hass.formatEntityState(stateObj) : stateObj.state;
  }

  _moreInfo(entityId) {
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      detail: { entityId }, bubbles: true, composed: true,
    }));
  }

  _bindClicks() {
    this.shadowRoot.querySelectorAll("[data-entity]").forEach((el) => {
      if (!el.dataset.entity) return;
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._moreInfo(el.dataset.entity);
      });
    });
    this.shadowRoot.querySelectorAll("[data-toggle]").forEach((el) => {
      if (!el.dataset.toggle) return;
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        this._hass.callService("switch", "toggle", { entity_id: el.dataset.toggle });
      });
    });
  }
}

class BoilerDemandCard extends BoilerDemandBase {
  static getConfigElement() {
    return document.createElement("boiler-demand-card-editor");
  }

  setConfig(config) {
    this._config = { show_controls: true, ...config };
    this._lastKey = undefined;
    this._render();
  }

  getCardSize() {
    return 3 + (this._model()?.rooms.length || 0);
  }

  _render() {
    if (!this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const hass = this._hass;
    if (!hass) return;

    const model = this._model();
    if (!model) {
      this.shadowRoot.innerHTML = `${STYLE}<ha-card><div class="empty">${esc(tr(hass, "no_device"))}</div></ha-card>`;
      return;
    }
    this._renderCard(hass, model);
    this._bindClicks();
  }


  _renderCard(hass, model) {
    const e = model.entities;
    const statusObj = hass.states[e.status];
    const boilerId = statusObj?.attributes?.boiler_switch;
    const boilerOn = hass.states[boilerId]?.state === "on";
    const demandOn = hass.states[e.heat_demand]?.state === "on";
    const remaining = statusObj?.attributes?.remaining_minutes;
    const onTh = numberState(hass, e.on_threshold);
    const offTh = numberState(hass, e.off_threshold);
    const autoTune = hass.states[e.auto_tune]?.state === "on";
    const starts = hass.states[e.boiler_starts_today]?.state ?? "–";
    const maxValve = numberState(hass, e.max_valve_opening);
    const title = this._config.title || deviceName(model.main);
    const statusClass = statusObj?.state === "failsafe" || statusObj?.state === "valves_offline"
      ? "warn" : statusObj?.state === "disabled" ? "muted" : "";
    // "Heating"/"Idle" just repeat the big boiler line; show the status only when it adds something.
    const redundant = (boilerOn && statusObj?.state === "heating") || (!boilerOn && statusObj?.state === "idle");
    const statusLine = redundant ? "" : `
            <div class="status ${statusClass}" data-entity="${esc(e.status || "")}">
              ${esc(this._fmt(e.status))}${remaining != null ? ` · ${esc(remaining)} ${esc(tr(hass, "left"))}` : ""}
            </div>`;

    // Room summary: count, calling for heat (demand/holding), offline, average temperature.
    const roles = model.rooms.map((room) => {
      const id = room.entities.valve_opening;
      return (id && hass.states[id]?.attributes?.role) || "offline";
    });
    const calling = roles.filter((r) => r === "demand" || r === "holding").length;
    const offline = roles.filter((r) => r === "offline").length;
    const temps = model.rooms
      .map((room) => (room.entities.room_temperature ? numberState(hass, room.entities.room_temperature) : null))
      .filter((t) => t !== null);
    const tempUnit = model.rooms
      .map((room) => hass.states[room.entities.room_temperature]?.attributes?.unit_of_measurement)
      .find(Boolean) || "°C";
    const avgTemp = temps.length ? (temps.reduce((a, b) => a + b, 0) / temps.length).toFixed(1) : null;
    const roomStat = model.rooms.length ? `
          <div class="stat"><b>${model.rooms.length}</b> ${esc(tr(hass, model.rooms.length === 1 ? "room" : "rooms"))}${
            calling ? ` · <b>${calling}</b> ${esc(tr(hass, "calling"))}` : ""}${
            offline ? ` · <span class="warn-text"><b>${offline}</b> ${esc(tr(hass, "offline"))}</span>` : ""}</div>
          ${avgTemp !== null ? `<div class="stat">${esc(tr(hass, "avg"))} <b>${esc(avgTemp)} ${esc(tempUnit)}</b></div>` : ""}` : "";

    const rooms = model.rooms.map((room) => {
      const valveId = room.entities.valve_opening;
      const tempId = room.entities.room_temperature;
      const valve = valveId ? numberState(hass, valveId) : null;
      const roleRaw = valveId ? hass.states[valveId]?.attributes?.role : undefined;
      const role = roleRaw || (valve === null ? "offline" : "closed");
      const temp = tempId ? this._fmt(tempId) : "";
      const th = roomThresholds(hass, room, autoTune, onTh, offTh);
      const holdId = room.entities.holding_level;
      const hold = hass.states[holdId];
      const holding = numberState(hass, holdId);
      const stuck = hold?.attributes?.stuck_open === true;
      const needed = hold?.attributes?.samples_needed ?? 3;
      const learned = holding !== null
        ? `${tr(hass, "holding")} ${Math.round(holding)} %`
        : `${tr(hass, "learning")} ${Math.min(hold?.attributes?.samples ?? 0, needed)}/${needed}`;
      const tuneLine = hold && e.auto_tune ? `
          <div class="room-tune ${autoTune ? "" : "inactive"}" data-entity="${esc(holdId)}">
            ≋ ${esc(learned)} · ${esc(tr(hass, "on_th"))} ${hold.attributes.on_threshold ?? "–"} / ${esc(tr(hass, "off_th"))} ${hold.attributes.off_threshold ?? "–"}${autoTune ? "" : ` (${esc(tr(hass, "inactive"))})`}
          </div>` : "";
      const marks = [
        th.off !== null ? `<div class="mark" style="left:${th.off}%" title="${esc(tr(hass, "off_th"))} ${th.off}%"></div>` : "",
        th.on !== null ? `<div class="mark on" style="left:${th.on}%" title="${esc(tr(hass, "on_th"))} ${th.on}%"></div>` : "",
      ].join("");
      return `
        <div class="room" data-entity="${esc(valveId || tempId || "")}">
          <div class="room-name">${esc(room.name)}${stuck ? ` <span class="warn-text" title="${esc(tr(hass, "stuck"))}">⚠</span>` : ""}</div>
          <div class="room-temp" data-entity="${esc(tempId || "")}">${esc(temp)}</div>
          <div class="bar role-${esc(role)}">
            <div class="fill" style="width:${valve === null ? 0 : Math.min(valve, 100)}%"></div>
            ${marks}
          </div>
          <div class="room-valve">${valve === null ? esc(tr(hass, "offline")) : `${Math.round(valve)} %`}</div>
          ${tuneLine}
        </div>`;
    }).join("");

    const controls = this._config.show_controls ? `
      <div class="controls">
        <button class="chip ${hass.states[e.automatic_control]?.state === "on" ? "active" : ""}" data-toggle="${esc(e.automatic_control || "")}">
          <ha-icon icon="mdi:robot"></ha-icon>${esc(tr(hass, "auto"))}
        </button>
        ${e.auto_tune ? `<button class="chip ${autoTune ? "active" : ""}" data-toggle="${esc(e.auto_tune)}">
          <ha-icon icon="mdi:tune-variant"></ha-icon>${esc(tr(hass, "auto_tune"))}
        </button>` : ""}
        <button class="chip" data-entity="${esc(e.on_threshold || "")}"${autoTune ? ` title="${esc(tr(hass, "th_floor"))}"` : ""}>${esc(tr(hass, "on_th"))} ${autoTune ? "≥ " : ""}${onTh ?? "–"} %</button>
        <button class="chip" data-entity="${esc(e.off_threshold || "")}"${autoTune ? ` title="${esc(tr(hass, "th_floor"))}"` : ""}>${esc(tr(hass, "off_th"))} ${autoTune ? "≥ " : ""}${offTh ?? "–"} %</button>
      </div>` : "";

    this.shadowRoot.innerHTML = `${STYLE}
      <ha-card>
        <div class="header">
          <div class="title">${esc(title)}</div>
        </div>
        <div class="hero" data-entity="${esc(boilerId || "")}">
          <div class="flame ${boilerOn ? "on" : ""}"><ha-icon icon="${boilerOn ? "mdi:fire" : "mdi:fire-off"}"></ha-icon></div>
          <div class="hero-text">
            <div class="boiler">${esc(tr(hass, boilerOn ? "heating" : "off"))}</div>${statusLine}
          </div>
        </div>
        <div class="stats">
          <div class="stat" data-entity="${esc(e.heat_demand || "")}"><span class="dot ${demandOn ? "on" : ""}"></span>${esc(tr(hass, demandOn ? "demand" : "no_demand"))}</div>
          <div class="stat" data-entity="${esc(e.max_valve_opening || "")}">${esc(tr(hass, "max"))} <b>${maxValve === null ? "–" : `${Math.round(maxValve)} %`}</b></div>
          <div class="stat" data-entity="${esc(e.boiler_starts_today || "")}"><b>${esc(starts)}</b> ${esc(tr(hass, "starts"))}</div>
          ${roomStat}
        </div>
        ${rooms ? `<div class="rooms">${rooms}</div>` : ""}
        ${controls}
      </ha-card>`;
  }

}

const STYLE = `<style>
  ha-card { padding: 16px; }
  .empty { color: var(--secondary-text-color); }
  .header { display: flex; justify-content: space-between; align-items: baseline; }
  .title { font-size: 1.1em; font-weight: 500; color: var(--primary-text-color); }
  [data-entity], [data-toggle] { cursor: pointer; }
  .hero { display: flex; align-items: center; gap: 14px; margin: 14px 0 10px; }
  .flame {
    width: 52px; height: 52px; border-radius: 50%; flex: none;
    display: flex; align-items: center; justify-content: center;
    background: var(--secondary-background-color); color: var(--disabled-text-color, #9e9e9e);
  }
  .flame.on {
    background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 18%, transparent);
    color: var(--state-climate-heat-color, #ff8100);
  }
  .flame ha-icon { --mdc-icon-size: 30px; }
  .boiler { font-size: 1.6em; font-weight: 600; line-height: 1.1; color: var(--primary-text-color); }
  .status { color: var(--secondary-text-color); margin-top: 2px; }
  .status.warn { color: var(--warning-color, #f5a623); font-weight: 500; }
  .status.muted { color: var(--disabled-text-color, #9e9e9e); }
  .stats { display: flex; flex-wrap: wrap; gap: 6px 16px; color: var(--secondary-text-color); font-size: 0.9em; }
  .stat b { color: var(--primary-text-color); font-weight: 500; }
  .warn-text, .warn-text b { color: var(--warning-color, #f5a623); }
  .dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px;
    background: var(--disabled-text-color, #9e9e9e);
  }
  .dot.on { background: var(--state-climate-heat-color, #ff8100); }
  .rooms { margin-top: 14px; border-top: 1px solid var(--divider-color); padding-top: 10px; display: grid; gap: 10px; }
  .room {
    display: grid; grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: "name temp" "bar valve" "tune tune";
    gap: 4px 10px; align-items: center;
  }
  .room-name { grid-area: name; color: var(--primary-text-color); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .room-temp { grid-area: temp; color: var(--primary-text-color); font-weight: 500; text-align: right; }
  .room-tune { grid-area: tune; font-size: 0.8em; color: var(--secondary-text-color); margin-top: -2px; }
  .room-tune.inactive { opacity: 0.6; }
  .room-valve { grid-area: valve; color: var(--secondary-text-color); font-size: 0.85em; min-width: 44px; text-align: right; }
  .bar {
    grid-area: bar; position: relative; height: 8px; border-radius: 4px;
    background: var(--secondary-background-color); overflow: visible;
  }
  .fill { height: 100%; border-radius: 4px; background: var(--disabled-text-color, #9e9e9e); transition: width 0.4s; }
  .role-demand .fill { background: var(--state-climate-heat-color, #ff8100); }
  .role-holding .fill { background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 55%, var(--secondary-background-color)); }
  .role-offline { background: repeating-linear-gradient(45deg, var(--secondary-background-color) 0 6px, transparent 6px 12px); }
  .mark {
    position: absolute; top: -3px; width: 2px; height: 14px; margin-left: -1px;
    background: var(--secondary-text-color); opacity: 0.5; border-radius: 1px;
  }
  .mark.on { opacity: 0.9; }
  .controls { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
  .chip {
    display: inline-flex; align-items: center; gap: 4px; font: inherit; font-size: 0.85em;
    color: var(--primary-text-color); background: var(--secondary-background-color);
    border: 1px solid var(--divider-color); border-radius: 16px; padding: 4px 10px; cursor: pointer;
  }
  .chip ha-icon { --mdc-icon-size: 16px; }
  .chip.active { border-color: var(--primary-color); color: var(--primary-color); }
</style>`;

class BoilerDemandCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => tr(this._hass, schema.name === "device_id" ? "device" : schema.name);
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(new CustomEvent("config-changed", {
          detail: { config: this._config }, bubbles: true, composed: true,
        }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = { show_controls: true, ...this._config };
    this._form.schema = [
      { name: "device_id", selector: { device: { filter: [{ integration: DOMAIN, model: MAIN_MODEL }] } } },
      { name: "title", selector: { text: {} } },
      { name: "show_controls", selector: { boolean: {} } },
    ];
  }
}


const RANGES = { "24h": 24, "7d": 168 };
const REFRESH_MS = 5 * 60 * 1000;
const W = 1000; // SVG user units across the time axis

/** [{t, s}] sorted by time -> value at time t (last change before t). */
function valueAt(points, t) {
  let lo = 0, hi = points.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (points[mid].t <= t) { found = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  return found < 0 ? undefined : points[found].s;
}

function num(state) {
  const v = parseFloat(state);
  return Number.isFinite(v) ? v : null;
}

/** Constant-value segments [{t0, t1, s}] clipped to [start, end]. */
function segments(points, start, end) {
  const out = [];
  points.forEach((p, i) => {
    const t0 = Math.max(p.t, start);
    const t1 = Math.min(i + 1 < points.length ? points[i + 1].t : end, end);
    if (t1 > t0) out.push({ t0, t1, s: p.s });
  });
  return out;
}

/** Boiler on time, starts and longest run from the switch history. */
function boilerStats(points, start, end) {
  let onMs = 0, starts = 0, longest = 0, runStart = null;
  let prev = valueAt(points, start);
  if (prev === "on") runStart = start;
  for (const seg of segments(points, start, end)) {
    if (seg.s === "on") {
      onMs += seg.t1 - seg.t0;
      if (prev !== "on" && seg.t0 > start) starts += 1;
      if (runStart === null) runStart = seg.t0;
    } else if (runStart !== null) {
      longest = Math.max(longest, seg.t0 - runStart);
      runStart = null;
    }
    prev = seg.s;
  }
  if (runStart !== null) longest = Math.max(longest, end - runStart);
  return { onMs, starts, longest };
}

/**
 * SVG path for recorded values: hold each value until the next change, then step.
 * The recorder stores changes only, so a straight line between two points would
 * invent a slow ramp over hours where the value really stayed flat.
 */
function holdPath(points, x, y, start) {
  let d = "";
  points.forEach((p, i) => {
    const px = x(Math.max(p.t, start));
    if (i) d += `L${px},${y(points[i - 1].v)}`;
    d += `${i ? "L" : "M"}${px},${y(p.v)}`;
  });
  return points.length ? `${d}L${W},${y(points[points.length - 1].v)}` : "";
}

/** demand / holding / closed / offline for a valve opening (same rule as the integration). */
function roleOf(state, th) {
  const v = num(state);
  if (v === null || v < 0) return "offline";
  if (th.on !== null && v >= th.on) return "demand";
  if (th.off !== null && v > th.off) return "holding";
  return "closed";
}

function fmtHours(ms) {
  const h = ms / 3600e3;
  return h >= 10 ? `${Math.round(h)} h` : `${h.toFixed(1)} h`;
}

class BoilerDemandHistoryCard extends BoilerDemandBase {
  static getConfigElement() {
    return document.createElement("boiler-demand-history-card-editor");
  }

  setConfig(config) {
    this._config = { range: "7d", ...config };
    this._range = this._config.range in RANGES ? this._config.range : "7d";
    this._history = null;
    this._fetchedKey = undefined;
    this._lastKey = undefined;
    this._render();
  }

  connectedCallback() {
    this._timer = setInterval(() => this._fetch(), REFRESH_MS);
    if (this._hass) this._fetch();
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  getCardSize() {
    return 4 + 2 * (this._model()?.rooms.length || 0);
  }

  /** Entities to chart: source sensors (longer history than the mirrors). */
  _historyIds(model) {
    const states = this._hass.states;
    const status = states[model.entities.status]?.attributes || {};
    const source = (id) => states[id]?.attributes?.source_entity || id;
    return {
      boiler: status.boiler_switch,
      flow: status.flow_sensor,
      rooms: model.rooms.map((r) => ({
        valve: r.entities.valve_opening ? source(r.entities.valve_opening) : undefined,
        temp: r.entities.room_temperature ? source(r.entities.room_temperature) : undefined,
      })),
    };
  }

  _hassChanged() {
    const model = this._model();
    if (!model) return;
    const key = `${this._range}|${JSON.stringify(this._historyIds(model))}`;
    if (key !== this._fetchedKey) {
      this._fetchedKey = key;
      this._fetch();
    }
  }

  async _fetch() {
    const hass = this._hass;
    const model = hass && this._model();
    if (!model) return;
    const ids = this._historyIds(model);
    const wanted = [...new Set([ids.boiler, ids.flow, ...ids.rooms.flatMap((r) => [r.valve, r.temp])].filter(Boolean))];
    const end = Date.now();
    const start = end - RANGES[this._range] * 3600e3;
    const range = this._range;
    try {
      const result = await hass.callWS({
        type: "history/history_during_period",
        start_time: new Date(start).toISOString(),
        end_time: new Date(end).toISOString(),
        entity_ids: wanted,
        minimal_response: true,
        no_attributes: true,
        significant_changes_only: false,
      });
      const series = {};
      for (const [id, items] of Object.entries(result || {})) {
        series[id] = items
          .map((it) => ({ t: (it.lu ?? it.lc) * 1000, s: it.s }))
          .filter((p) => Number.isFinite(p.t))
          .sort((a, b) => a.t - b.t);
      }
      if (range !== this._range) return; // switched while loading
      this._history = { start, end, series, ids };
      this._error = null;
    } catch (err) {
      this._error = String(err?.message || err);
    }
    this._lastKey = undefined;
    this._render();
  }

  _render() {
    if (!this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    const hass = this._hass;
    if (!hass) return;
    const model = this._model();
    if (!model) {
      this.shadowRoot.innerHTML = `${STYLE}${HSTYLE}<ha-card><div class="empty">${esc(tr(hass, "no_device"))}</div></ha-card>`;
      return;
    }
    this._renderCard(hass, model);
    this._bindClicks();
    this.shadowRoot.querySelectorAll("[data-range]").forEach((el) => {
      el.addEventListener("click", (ev) => {
        ev.stopPropagation();
        if (this._range === el.dataset.range) return;
        this._range = el.dataset.range;
        this._history = null;
        this._fetchedKey = undefined;
        this._lastKey = undefined;
        this._render();
        this._hassChanged();
      });
    });
    this._bindHover();
  }

  _renderCard(hass, model) {
    const e = model.entities;
    const h = this._history;
    const autoTune = hass.states[e.auto_tune]?.state === "on";
    const onTh = numberState(hass, e.on_threshold);
    const offTh = numberState(hass, e.off_threshold);
    const title = this._config.title || deviceName(model.main);
    const rangeLabel = tr(hass, this._range === "24h" ? "last_24h" : "last_7d");

    const header = `
      <div class="header">
        <div class="title">${esc(title)} · ${esc(rangeLabel)}</div>
        <div class="chips">
          ${Object.keys(RANGES).map((r) => `<button class="chip ${r === this._range ? "active" : ""}" data-range="${r}">${r}</button>`).join("")}
        </div>
      </div>`;

    if (!h) {
      const msg = this._error ? `${tr(hass, "no_history")} (${this._error})` : tr(hass, "loading");
      this.shadowRoot.innerHTML = `${STYLE}${HSTYLE}<ha-card>${header}<div class="empty pad">${esc(msg)}</div></ha-card>`;
      return;
    }

    const x = (t) => ((t - h.start) / (h.end - h.start)) * W;
    const pct = (t) => `${((t - h.start) / (h.end - h.start)) * 100}%`;

    // Time axis: midnights (7 d) or every 6 h (24 h), in local time.
    const ticks = [];
    const step = this._range === "24h" ? 6 : 24;
    const first = new Date(h.start);
    first.setMinutes(0, 0, 0);
    first.setHours(Math.ceil(first.getHours() / step) * step);
    for (let t = first.getTime(); t < h.end; t += step * 3600e3) {
      const d = new Date(t);
      if (step === 24) d.setHours(0, 0, 0, 0);
      ticks.push(d.getTime());
    }
    const lang = hass.locale?.language || hass.language || "en";
    const tickLabel = (t) => step === 24
      ? new Date(t).toLocaleDateString(lang, { weekday: "short", day: "numeric" })
      : new Date(t).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" });
    const grid = ticks.map((t) => `<line class="grid" x1="${x(t)}" x2="${x(t)}" y1="0" y2="100" />`).join("");
    const axis = `
      <div class="axis">${ticks
        .map((t) => ({ t, at: step === 24 ? t + 12 * 3600e3 : t }))
        .filter(({ at }) => at > h.start && at < h.end)
        .map(({ t, at }) => `<span style="left:${pct(at)}">${esc(tickLabel(t))}</span>`).join("")}</div>`;

    // Boiler row + summary.
    const boilerPts = h.series[h.ids.boiler] || [];
    const stats = boilerStats(boilerPts, h.start, h.end);
    const days = (h.end - h.start) / 86400e3;
    const onRects = segments(boilerPts, h.start, h.end)
      .filter((seg) => seg.s === "on")
      .map((seg) => `<rect x="${x(seg.t0)}" width="${Math.max(x(seg.t1) - x(seg.t0), 1)}" y="0" height="100" />`)
      .join("");
    const summary = `
      <div class="summary" data-entity="${esc(h.ids.boiler || "")}">
        <ha-icon icon="mdi:fire"></ha-icon>
        <b>${fmtHours(stats.onMs)}</b> ${esc(tr(hass, "on_for"))} (${Math.round((100 * stats.onMs) / (h.end - h.start))} %)
        · <b>${stats.starts}</b> ${esc(tr(hass, "starts_n"))}${days > 1.5 ? ` (${(stats.starts / days).toFixed(1)}${esc(tr(hass, "per_day"))})` : ""}
        · ${esc(tr(hass, "longest"))} <b>${fmtHours(stats.longest)}</b>
      </div>`;
    const boilerRow = `
      <div class="row">
        <div class="label">${esc(tr(hass, "boiler"))}</div>
        <div class="chart small" data-kind="boiler" data-series="${esc(h.ids.boiler || "")}">
          <svg viewBox="0 0 ${W} 100" preserveAspectRatio="none">${grid}<g class="on">${onRects}</g></svg>
          <div class="cursor"></div><div class="tip"></div>
        </div>
      </div>`;

    // Flow temperature (only with a flow sensor).
    let flowRow = "";
    const flowPts = (h.series[h.ids.flow] || []).map((p) => ({ t: p.t, v: num(p.s) })).filter((p) => p.v !== null);
    if (h.ids.flow && flowPts.length) {
      const flowUnit = hass.states[h.ids.flow]?.attributes?.unit_of_measurement || "°C";
      const fahrenheit = flowUnit.includes("F");
      const lo = Math.min(fahrenheit ? 68 : 20, ...flowPts.map((p) => p.v));
      const hi = Math.max(fahrenheit ? 140 : 60, ...flowPts.map((p) => p.v));
      const y = (v) => 100 - ((v - lo) / (hi - lo)) * 100;
      const line = holdPath(flowPts, x, y, h.start);
      flowRow = `
        <div class="row">
          <div class="label">${esc(tr(hass, "flow"))}</div>
          <div class="chart mid" data-kind="flow" data-series="${esc(h.ids.flow)}">
            <svg viewBox="0 0 ${W} 100" preserveAspectRatio="none">${grid}<path class="flow" d="${line}" /></svg>
            <div class="scale">${Math.round(lo)}–${Math.round(hi)} ${esc(flowUnit)}</div>
            <div class="cursor"></div><div class="tip"></div>
          </div>
        </div>`;
    }

    // One chart per room: valve area, temperature line, learned holding level.
    const ROLE_ICON = { demand: "▲", holding: "◆", closed: "─", offline: "✕" };
    const rooms = model.rooms.map((room, i) => {
      const ids = h.ids.rooms[i] || {};
      const valvePts = h.series[ids.valve] || [];
      const tempPts = (h.series[ids.temp] || []).map((p) => ({ t: p.t, v: num(p.s) })).filter((p) => p.v !== null);
      const valveId = room.entities.valve_opening;
      const tempId = room.entities.room_temperature;
      const holdId = room.entities.holding_level;
      const role = hass.states[valveId]?.attributes?.role || "offline";
      const hold = hass.states[holdId];
      const holding = num(hold?.state);
      const stuck = hold?.attributes?.stuck_open === true;
      const samples = hold?.attributes?.samples ?? 0;
      const needed = hold?.attributes?.samples_needed ?? 3;
      const tune = !holdId ? "" : holding !== null
        ? `${esc(tr(hass, "holding"))} ${Math.round(holding)} % · ${esc(tr(hass, "on_th"))} ${hold.attributes.on_threshold} / ${esc(tr(hass, "off_th"))} ${hold.attributes.off_threshold}`
        : `${esc(tr(hass, "learning"))} ${Math.min(samples, needed)}/${needed}`;

      let area = `M0,100`;
      for (const seg of segments(valvePts, h.start, h.end)) {
        const v = num(seg.s);
        const yv = v === null || v < 0 ? 100 : 100 - Math.min(v, 100);
        area += `L${x(seg.t0)},${yv}L${x(seg.t1)},${yv}`;
      }
      area += `L${W},100Z`;

      let tempLine = "";
      let scale = "";
      if (tempPts.length) {
        const lo = Math.floor(Math.min(...tempPts.map((p) => p.v)) - 0.5);
        const hi = Math.ceil(Math.max(...tempPts.map((p) => p.v)) + 0.5);
        scale = `<div class="scale">${lo}–${hi} ${esc(hass.states[ids.temp]?.attributes?.unit_of_measurement || "°C")}</div>`;
        const y = (v) => 100 - ((v - lo) / (hi - lo)) * 100;
        tempLine = `<path class="temp" d="${holdPath(tempPts, x, y, h.start)}" />`;
      }
      // When this room called for heat, from its valve and its thresholds.
      const th = roomThresholds(hass, room, autoTune, onTh, offTh);
      const demandRects = segments(valvePts, h.start, h.end)
        .map((seg) => ({ ...seg, role: roleOf(seg.s, th) }))
        .filter((seg) => seg.role === "demand" || seg.role === "holding")
        .map((seg) => `<rect class="r-${seg.role}" x="${x(seg.t0)}" width="${Math.max(x(seg.t1) - x(seg.t0), 1)}" y="0" height="100" />`)
        .join("");
      this._thresholds = { ...(this._thresholds || {}), [ids.valve]: th };
      const holdLine = holding !== null ? `<line class="hold" x1="0" x2="${W}" y1="${100 - holding}" y2="${100 - holding}" />` : "";

      return `
        <div class="room-h">
          <div class="room-head" data-entity="${esc(valveId || "")}">
            <span class="name">${esc(room.name)}${stuck ? ` <span class="warn-text" title="${esc(tr(hass, "stuck"))}">⚠</span>` : ""}</span>
            <span class="now" data-entity="${esc(tempId || "")}">${tempId ? esc(this._fmt(tempId)) : ""}</span>
            <span class="role role-${esc(role)}">${ROLE_ICON[role] || ""} ${esc(tr(hass, `role_${role}`))}</span>
            <span class="tune" data-entity="${esc(holdId || "")}">${tune}</span>
          </div>
          ${stuck ? `<div class="warn-text small-text">⚠ ${esc(tr(hass, "stuck"))}</div>` : ""}
          <div class="chart" data-kind="room" data-series="${esc(ids.valve || "")}" data-temp="${esc(ids.temp || "")}">
            <svg viewBox="0 0 ${W} 100" preserveAspectRatio="none">${grid}<path class="valve" d="${area}" />${holdLine}${tempLine}</svg>
            ${scale}
            <div class="cursor"></div><div class="tip"></div>
          </div>
          <div class="chart strip" data-kind="demand" data-series="${esc(ids.valve || "")}">
            <svg viewBox="0 0 ${W} 100" preserveAspectRatio="none">${grid}${demandRects}</svg>
            <div class="cursor"></div><div class="tip"></div>
          </div>
        </div>`;
    }).join("");

    this.shadowRoot.innerHTML = `${STYLE}${HSTYLE}
      <ha-card>
        ${header}
        ${summary}
        <div class="timeline">
          ${axis}
          ${boilerRow}
          ${flowRow}
        </div>
        <div class="rooms-h">${rooms}</div>
        <div class="legend">
          <span><i class="lg-valve"></i>${esc(tr(hass, "valve"))} 0–100 %</span>
          <span><i class="lg-temp"></i>${esc(tr(hass, "temperature"))}</span>
          <span><i class="lg-hold"></i>${esc(tr(hass, "holding_level"))}</span>
          <span><i class="lg-demand"></i>${esc(tr(hass, "role_demand"))}</span>
          <span><i class="lg-holding"></i>${esc(tr(hass, "role_holding"))}</span>
        </div>
      </ha-card>`;
  }

  /** Synchronised cursor over all charts with a value tooltip on the hovered one. */
  _bindHover() {
    const h = this._history;
    if (!h) return;
    const charts = [...this.shadowRoot.querySelectorAll(".chart")];
    const lang = this._hass.locale?.language || this._hass.language || "en";
    const hide = () => charts.forEach((c) => c.classList.remove("hover"));
    charts.forEach((chart) => {
      chart.addEventListener("pointermove", (ev) => {
        const rect = chart.getBoundingClientRect();
        const frac = Math.min(Math.max((ev.clientX - rect.left) / rect.width, 0), 1);
        const t = h.start + frac * (h.end - h.start);
        charts.forEach((c) => {
          c.classList.add("hover");
          c.querySelector(".cursor").style.left = `${frac * 100}%`;
          c.querySelector(".tip").textContent = "";
        });
        const when = new Date(t).toLocaleString(lang, { weekday: "short", hour: "2-digit", minute: "2-digit" });
        const raw = valueAt(h.series[chart.dataset.series] || [], t);
        let text = when;
        if (chart.dataset.kind === "boiler") text += ` · ${raw === "on" ? tr(this._hass, "heating") : tr(this._hass, "off")}`;
        else if (chart.dataset.kind === "demand") {
          const th = this._thresholds?.[chart.dataset.series] || { on: null, off: null };
          text += ` · ${tr(this._hass, `role_${roleOf(raw, th)}`)}`;
        }
        else if (chart.dataset.kind === "flow") {
          const unit = this._hass.states[chart.dataset.series]?.attributes?.unit_of_measurement || "°C";
          text += num(raw) !== null ? ` · ${num(raw).toFixed(1)} ${unit}` : "";
        }
        else {
          const v = num(raw);
          const temp = num(valueAt(h.series[chart.dataset.temp] || [], t));
          text += ` · ${tr(this._hass, "valve")} ${v === null || v < 0 ? "–" : `${Math.round(v)} %`}`;
          if (temp !== null) text += ` · ${temp.toFixed(1)} ${this._hass.states[chart.dataset.temp]?.attributes?.unit_of_measurement || "°C"}`;
        }
        const tip = chart.querySelector(".tip");
        tip.textContent = text;
        tip.style.left = frac > 0.6 ? "auto" : `calc(${frac * 100}% + 6px)`;
        tip.style.right = frac > 0.6 ? `calc(${(1 - frac) * 100}% + 6px)` : "auto";
      });
      chart.addEventListener("pointerleave", hide);
    });
  }
}

const HSTYLE = `<style>
  .header { flex-wrap: wrap; gap: 6px; align-items: center; }
  .chips { display: flex; gap: 6px; flex-wrap: wrap; }
  .pad { padding: 16px 0 4px; }
  .summary { margin: 10px 0 8px; color: var(--secondary-text-color); font-size: 0.9em; }
  .summary b { color: var(--primary-text-color); font-weight: 500; }
  .summary ha-icon { --mdc-icon-size: 16px; color: var(--state-climate-heat-color, #ff8100); vertical-align: -2px; }
  .timeline { display: grid; gap: 4px; }
  .axis { position: relative; height: 16px; margin-left: 64px; font-size: 0.75em; color: var(--secondary-text-color); }
  .axis span { position: absolute; transform: translateX(-50%); white-space: nowrap; }
  .row { display: grid; grid-template-columns: 60px minmax(0, 1fr); gap: 4px; align-items: center; }
  .label { font-size: 0.8em; color: var(--secondary-text-color); }
  .chart { position: relative; height: 56px; background: var(--secondary-background-color); border-radius: 4px; touch-action: pan-y; }
  .chart.small { height: 16px; }
  .chart.mid { height: 32px; }
  .chart.strip { height: 6px; margin-top: 2px; border-radius: 2px; }
  .chart.strip .tip { top: 8px; }
  .r-demand, .lg-demand { fill: var(--state-climate-heat-color, #ff8100); background: var(--state-climate-heat-color, #ff8100); }
  .r-holding, .lg-holding {
    fill: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 40%, var(--secondary-background-color));
    background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 40%, var(--secondary-background-color));
  }
  .scale {
    position: absolute; top: 1px; right: 4px; font-size: 0.7em; line-height: 1.2; pointer-events: none;
    color: var(--secondary-text-color); background: color-mix(in srgb, var(--secondary-background-color) 80%, transparent);
    padding: 0 3px; border-radius: 3px;
  }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; margin-top: 10px; font-size: 0.75em; color: var(--secondary-text-color); }
  .legend i { display: inline-block; width: 14px; height: 8px; margin-right: 5px; vertical-align: 0; border-radius: 2px; }
  .lg-valve { background: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 45%, transparent); }
  .lg-temp { height: 2px !important; vertical-align: 3px !important; background: var(--primary-color); }
  .lg-hold { height: 0 !important; vertical-align: 3px !important; border-top: 1px dashed var(--primary-text-color); }
  .chart svg { position: absolute; inset: 0; width: 100%; height: 100%; overflow: visible; }
  .grid { stroke: var(--divider-color); stroke-width: 1; vector-effect: non-scaling-stroke; }
  .on rect { fill: var(--state-climate-heat-color, #ff8100); }
  .valve { fill: color-mix(in srgb, var(--state-climate-heat-color, #ff8100) 45%, transparent); stroke: none; }
  .temp { fill: none; stroke: var(--primary-color); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .flow { fill: none; stroke: var(--error-color, #db4437); stroke-width: 1.5; vector-effect: non-scaling-stroke; }
  .hold { stroke: var(--primary-text-color); stroke-width: 1; stroke-dasharray: 4 3; opacity: 0.6; vector-effect: non-scaling-stroke; }
  .cursor { display: none; position: absolute; top: 0; bottom: 0; width: 1px; background: var(--primary-text-color); opacity: 0.5; pointer-events: none; }
  .tip {
    display: none; position: absolute; top: 2px; white-space: nowrap; pointer-events: none; z-index: 1;
    font-size: 0.75em; padding: 1px 6px; border-radius: 4px;
    background: var(--card-background-color, #fff); color: var(--primary-text-color); box-shadow: 0 1px 3px rgba(0,0,0,0.3);
  }
  .chart.hover .cursor { display: block; }
  .chart.hover .tip:not(:empty) { display: block; }
  .rooms-h { display: grid; gap: 12px; margin-top: 14px; border-top: 1px solid var(--divider-color); padding-top: 10px; }
  .room-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 2px 10px; margin-bottom: 4px; }
  .room-head .name { color: var(--primary-text-color); font-weight: 500; }
  .room-head .now { color: var(--primary-text-color); }
  .room-head .role { font-size: 0.85em; color: var(--secondary-text-color); }
  .room-head .role-demand { color: var(--state-climate-heat-color, #ff8100); }
  .room-head .tune { font-size: 0.85em; color: var(--secondary-text-color); margin-left: auto; }
  .small-text { font-size: 0.8em; margin-bottom: 4px; }
</style>`;

class BoilerDemandHistoryCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = config;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;
    if (!this._form) {
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => tr(this._hass, schema.name === "device_id" ? "device" : schema.name);
      this._form.addEventListener("value-changed", (ev) => {
        this._config = ev.detail.value;
        this.dispatchEvent(new CustomEvent("config-changed", {
          detail: { config: this._config }, bubbles: true, composed: true,
        }));
      });
      this.appendChild(this._form);
    }
    this._form.hass = this._hass;
    this._form.data = { range: "7d", ...this._config };
    this._form.schema = [
      { name: "device_id", selector: { device: { filter: [{ integration: DOMAIN, model: MAIN_MODEL }] } } },
      { name: "title", selector: { text: {} } },
      { name: "range", selector: { select: { mode: "dropdown", options: Object.keys(RANGES) } } },
    ];
  }
}

if (!customElements.get("boiler-demand-card")) {
  customElements.define("boiler-demand-card", BoilerDemandCard);
  customElements.define("boiler-demand-card-editor", BoilerDemandCardEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "boiler-demand-card",
    name: "Boiler Demand",
    description: "Boiler state, demand status and every room with temperature and valve opening.",
    preview: true,
    documentationURL: "https://github.com/tuct/HomeAssistant-Bosch-Boiler",
  });
  customElements.define("boiler-demand-history-card", BoilerDemandHistoryCard);
  customElements.define("boiler-demand-history-card-editor", BoilerDemandHistoryCardEditor);
  window.customCards.push({
    type: "boiler-demand-history-card",
    name: "Boiler Demand history",
    description: "Valves, room temperatures and boiler runs over the last 24 hours or 7 days, with auto-tune info.",
    preview: true,
    documentationURL: "https://github.com/tuct/HomeAssistant-Bosch-Boiler",
  });
  console.info(`%c BOILER-DEMAND-CARD %c ${CARD_VERSION} `, "background:#ff8100;color:#fff", "");
}
