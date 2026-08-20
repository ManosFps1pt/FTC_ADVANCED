import { useEffect, useMemo, useState } from "react";

type ConfigurationFile = { name: string; location?: string };
type ConfigurationDocument = { name: string; xml: string };
type Group = "motor" | "servo" | "digital" | "pwm" | "analog" | "i2c";

type DeviceType = { label: string; tag: string; group: Group };
type Device = {
  tag: string;
  label: string;
  group: Group;
  name: string;
  port: number;
  bus?: number;
  source: "auto" | "user";
  address?: string;
  attributes: Record<string, string>;
};
type Hub = { name: string; address: number; attributes: Record<string, string>; devices: Device[]; unknown: string[] };
type Portal = { name: string; serialNumber: string; parentModuleAddress: string; attributes: Record<string, string>; hubs: Hub[]; unknown: string[] };
type Webcam = { name: string; serialNumber: string; attributes: Record<string, string> };
type RobotConfig = { portals: Portal[]; webcams: Webcam[]; unknown: string[] };
type View = { page: "list" } | { page: "editor" } | { page: "hub"; portal: number; hub: number } | { page: "group"; portal: number; hub: number; group: Group; bus?: number };

const TYPES: DeviceType[] = [
  { label: "Unspecified Motor", tag: "Motor", group: "motor" },
  { label: "goBILDA 5201 Series", tag: "goBILDA5201SeriesMotor", group: "motor" },
  { label: "goBILDA 5202 Series", tag: "goBILDA5202SeriesMotor", group: "motor" },
  { label: "REV Robotics Core Hex Motor", tag: "RevRoboticsCoreHexMotor", group: "motor" },
  { label: "REV Robotics UltraPlanetary HD Hex Motor", tag: "RevRoboticsUltraplanetaryHDHexMotor", group: "motor" },
  { label: "Servo", tag: "Servo", group: "servo" },
  { label: "Continuous Rotation Servo", tag: "ContinuousRotationServo", group: "servo" },
  { label: "REV SPARKmini Controller", tag: "RevSPARKMini", group: "servo" },
  { label: "REV Blinkin LED Driver", tag: "RevBlinkinLedDriver", group: "servo" },
  { label: "Digital Device", tag: "DigitalDevice", group: "digital" },
  { label: "REV Touch Sensor", tag: "RevTouchSensor", group: "digital" },
  { label: "PWM Output", tag: "PwmOutput", group: "pwm" },
  { label: "Analog Input", tag: "AnalogInput", group: "analog" },
  { label: "REV Color/Range Sensor", tag: "LynxColorSensor", group: "i2c" },
  { label: "REV Color Sensor V3", tag: "RevColorSensorV3", group: "i2c" },
  { label: "REV 2M Distance Sensor", tag: "REV_VL53L0X_RANGE_SENSOR", group: "i2c" },
  { label: "Embedded Hub IMU", tag: "LynxEmbeddedIMU", group: "i2c" },
];

const GROUPS: { key: Group; label: string; ports?: number }[] = [
  { key: "motor", label: "Motors", ports: 4 },
  { key: "servo", label: "Servos", ports: 6 },
  { key: "digital", label: "Digital Devices", ports: 8 },
  { key: "pwm", label: "PWM Devices", ports: 6 },
  { key: "analog", label: "Analog Input Devices", ports: 8 },
];

function api<T>(path: string, options?: RequestInit): Promise<T> {
  return fetch(`/api${path}`, { headers: { "Content-Type": "application/json", ...options?.headers }, ...options })
    .then(async (response) => {
      if (response.ok) return response.status === 204 ? undefined as T : response.json() as Promise<T>;
      const body = await response.json().catch(() => null) as { detail?: string } | null;
      throw new Error(body?.detail ?? `Request failed (${response.status})`);
    });
}

function attrs(element: Element): Record<string, string> {
  return Object.fromEntries(Array.from(element.attributes, (attribute) => [attribute.name, attribute.value]));
}

function number(value: string | null, fallback = 0): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function inferGroup(element: Element): Group | null {
  if (element.hasAttribute("bus")) return "i2c";
  const found = TYPES.find((item) => item.tag === element.tagName);
  if (found) return found.group;
  if (/motor/i.test(element.tagName)) return "motor";
  if (/servo|blinkin/i.test(element.tagName)) return "servo";
  if (/digital|touch/i.test(element.tagName)) return "digital";
  if (/analog/i.test(element.tagName)) return "analog";
  return element.hasAttribute("port") ? "pwm" : null;
}

function deviceFrom(element: Element): Device | null {
  const group = inferGroup(element);
  if (!group) return null;
  const known = TYPES.find((item) => item.tag === element.tagName);
  const attributes = attrs(element);
  return {
    tag: element.tagName, label: known?.label ?? element.tagName, group,
    name: element.getAttribute("name") ?? "", port: number(element.getAttribute("port")),
    bus: element.hasAttribute("bus") ? number(element.getAttribute("bus")) : undefined,
    source: element.tagName === "LynxEmbeddedIMU" ? "auto" : "user",
    attributes,
  };
}

function parseRobot(xml: string): RobotConfig {
  const document = new DOMParser().parseFromString(xml, "application/xml");
  if (document.querySelector("parsererror") || document.documentElement.tagName !== "Robot") throw new Error("The Robot Controller returned invalid configuration XML.");
  const config: RobotConfig = { portals: [], webcams: [], unknown: [] };
  for (const child of Array.from(document.documentElement.children)) {
    if (child.tagName === "LynxUsbDevice") {
      const portal: Portal = {
        name: child.getAttribute("name") ?? "Hub Portal", serialNumber: child.getAttribute("serialNumber") ?? "",
        parentModuleAddress: child.getAttribute("parentModuleAddress") ?? "", attributes: attrs(child), hubs: [], unknown: [],
      };
      for (const portalChild of Array.from(child.children)) {
        if (portalChild.tagName !== "LynxModule") { portal.unknown.push(portalChild.outerHTML); continue; }
        const hub: Hub = { name: portalChild.getAttribute("name") ?? "REV Hub", address: number(portalChild.getAttribute("port")), attributes: attrs(portalChild), devices: [], unknown: [] };
        for (const hubChild of Array.from(portalChild.children)) {
          const device = deviceFrom(hubChild);
          if (device) hub.devices.push(device); else hub.unknown.push(hubChild.outerHTML);
        }
        portal.hubs.push(hub);
      }
      config.portals.push(portal);
    } else if (child.tagName === "Webcam") {
      config.webcams.push({ name: child.getAttribute("name") ?? "Webcam", serialNumber: child.getAttribute("serialNumber") ?? "", attributes: attrs(child) });
    } else config.unknown.push(child.outerHTML);
  }
  return config;
}

function escapeXml(value: string): string { return value.replace(/[<>&"']/g, (character) => ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", "\"": "&quot;", "'": "&apos;" })[character] ?? character); }
function attributesXml(attributes: Record<string, string>): string { return Object.entries(attributes).map(([key, value]) => ` ${key}="${escapeXml(value)}"`).join(""); }
function serializeRobot(config: RobotConfig): string {
  const deviceXml = (device: Device) => {
    const attributes: Record<string, string> = { ...device.attributes, name: device.name, port: String(device.port) };
    if (device.bus !== undefined) attributes.bus = String(device.bus); else delete attributes.bus;
    return `      <${device.tag}${attributesXml(attributes)} />`;
  };
  const portalXml = config.portals.map((portal) => {
    const portalAttrs = { ...portal.attributes, name: portal.name, serialNumber: portal.serialNumber, parentModuleAddress: portal.parentModuleAddress };
    return `  <LynxUsbDevice${attributesXml(portalAttrs)}>\n${portal.hubs.map((hub) => {
      const hubAttrs = { ...hub.attributes, name: hub.name, port: String(hub.address) };
      return `    <LynxModule${attributesXml(hubAttrs)}>\n${hub.devices.map(deviceXml).join("\n")}${hub.unknown.length ? `\n${hub.unknown.map((item) => `      ${item}`).join("\n")}` : ""}\n    </LynxModule>`;
    }).join("\n")}${portal.unknown.length ? `\n${portal.unknown.map((item) => `    ${item}`).join("\n")}` : ""}\n  </LynxUsbDevice>`;
  });
  const webcams = config.webcams.map((camera) => `  <Webcam${attributesXml({ ...camera.attributes, name: camera.name, serialNumber: camera.serialNumber })} />`);
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>\n<Robot type="FirstInspires-FTC">\n${[...portalXml, ...webcams, ...config.unknown.map((item) => `  ${item}`)].join("\n")}\n</Robot>\n`;
}

function cloneForNew(config: RobotConfig): RobotConfig {
  const copy = structuredClone(config);
  for (const portal of copy.portals) for (const hub of portal.hubs) hub.devices = hub.devices.filter((device) => device.source === "auto");
  return copy;
}

function deviceFor(type: DeviceType, port: number, bus?: number): Device {
  return { tag: type.tag, label: type.label, group: type.group, name: "", port, bus, source: "user", attributes: {} };
}

export function RobotConfiguration({ connected, robotState, startedOpmode }: { connected: boolean; robotState: string; startedOpmode: boolean }) {
  const [files, setFiles] = useState<ConfigurationFile[]>([]);
  const [active, setActive] = useState<ConfigurationFile | null>(null);
  const [config, setConfig] = useState<RobotConfig | null>(null);
  const [selected, setSelected] = useState("");
  const [saveName, setSaveName] = useState("");
  const [view, setView] = useState<View>({ page: "list" });
  const [notice, setNotice] = useState("Connect to the Robot Controller to retrieve its configurations.");
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);

  const canSave = connected && !busy && !startedOpmode && ["NOT_STARTED", "STOPPED"].includes(robotState) && Boolean(config?.portals.length) && Boolean(saveName.trim());
  const update = (change: (previous: RobotConfig) => RobotConfig) => { setConfig((previous) => previous ? change(structuredClone(previous)) : previous); setDirty(true); };

  const loadList = async (preferActive = true) => {
    if (!connected) return;
    setBusy(true);
    try {
      const [nextFiles, nextActive] = await Promise.all([api<ConfigurationFile[]>("/configurations"), api<ConfigurationFile>("/configurations/active")]);
      setFiles(nextFiles); setActive(nextActive);
      if (preferActive && !selected && nextFiles.some((file) => file.name === nextActive.name)) await load(nextActive.name, nextFiles);
      else setNotice("Choose a configuration, then select Edit to configure its hubs.");
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to retrieve configurations"); }
    finally { setBusy(false); }
  };

  const load = async (name: string, source = files) => {
    if (!name) return;
    setBusy(true);
    try {
      const document = await api<ConfigurationDocument>(`/configurations/${encodeURIComponent(name)}/xml`);
      setConfig(parseRobot(document.xml)); setSelected(document.name);
      setSaveName(source.find((file) => file.name === document.name)?.location === "RESOURCE" ? `${document.name} Copy` : document.name);
      setDirty(false); setNotice(`Loaded ${document.name}. Select a portal to configure its hubs.`);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to load configuration"); }
    finally { setBusy(false); }
  };

  useEffect(() => { if (connected) void loadList(); else { setFiles([]); setActive(null); setConfig(null); setSelected(""); setView({ page: "list" }); } }, [connected]);

  const startNew = () => {
    if (!config) { setNotice("First load a saved configuration. Its portal and module addresses are the scan-owned baseline for a new configuration."); return; }
    setConfig(cloneForNew(config)); setSelected(""); setSaveName("New Robot Configuration"); setDirty(true); setView({ page: "editor" });
    setNotice("New configuration started from the loaded scan. Automatic IMUs and scanned portal details were retained.");
  };

  const save = async () => {
    if (!config || !canSave) return;
    const invalid = config.portals.flatMap((portal) => portal.hubs).flatMap((hub) => hub.devices).find((device) => !device.name.trim());
    if (invalid) { setNotice(`Enter a hardware-map name for ${invalid.label} on port ${invalid.port}.`); return; }
    const duplicateName = config.portals.flatMap((portal) => portal.hubs).flatMap((hub) => hub.devices).map((device) => device.name.trim().toLocaleLowerCase()).filter((name, index, names) => name && names.indexOf(name) !== index)[0];
    if (duplicateName) { setNotice(`Hardware-map name “${duplicateName}” is used more than once.`); return; }
    const name = saveName.trim();
    if (!window.confirm(`Save and activate “${name}” on the Robot Controller? This changes its hardware map.`)) return;
    setBusy(true);
    try {
      const nextActive = await api<ConfigurationFile>("/configurations", { method: "PUT", body: JSON.stringify({ name, xml: serializeRobot(config) }) });
      setActive(nextActive); setSelected(nextActive.name); setDirty(false); setNotice(`${nextActive.name} was saved and is now active.`);
      await loadList(false);
    } catch (error) { setNotice(error instanceof Error ? error.message : "Unable to save configuration"); }
    finally { setBusy(false); }
  };

  const current = view.page === "hub" || view.page === "group" ? config?.portals[view.portal]?.hubs[view.hub] : undefined;
  const portal = view.page === "hub" || view.page === "group" ? config?.portals[view.portal] : undefined;
  const groupTypes = view.page === "group" ? TYPES.filter((item) => item.group === view.group) : [];
  const groupInfo = view.page === "group" ? GROUPS.find((item) => item.key === view.group) : undefined;
  const i2cDevices = view.page === "group" && view.group === "i2c" ? current?.devices.filter((device) => device.group === "i2c" && device.bus === view.bus) ?? [] : [];
  const i2cDuplicateAddresses = useMemo(() => i2cDevices.map((device) => device.address?.trim().toLowerCase()).filter((address): address is string => Boolean(address)).filter((address, index, all) => all.indexOf(address) !== index), [i2cDevices]);

  return <section className="configuration panel">
    <div className="panel-heading">
      <div><p className="eyebrow">Configure Robot</p><h2>Robot configuration {dirty ? <span className="dirty-indicator">Unsaved</span> : null}</h2></div>
      <div className="configuration-header-actions"><span className="configuration-active">Active: {active?.name ?? "Unknown"}</span><button className="secondary" type="button" disabled={!connected || busy} onClick={() => void loadList(false)}>Refresh</button></div>
    </div>
    <p className="configuration-safety">Use the Driver Station flow: retrieve a scanned configuration, open its portal and hub, choose a device type and hardware-map name, then save. XML is generated only when saved.</p>

    {view.page === "list" && <div className="configuration-list">
      <div className="configuration-list-actions"><button className="primary" type="button" disabled={!connected || busy || !config} onClick={startNew}>New from loaded scan</button><span>Scan-owned serial numbers and hub addresses are kept from the loaded Robot Controller configuration.</span></div>
      {connected && !config && <p className="configuration-help">To create the first configuration, use the official Driver Station: <strong>Configure Robot → New → Scan → Save</strong>. Then return here, select that scanned configuration, and use <strong>New from loaded scan</strong> for additional configurations. This app never guesses hub addresses or USB serial numbers.</p>}
      {files.length ? files.map((file) => <article className={`configuration-file ${file.name === selected ? "selected" : ""}`} key={file.name}><div><strong>{file.name}</strong><small>{file.name === active?.name ? "Active" : file.location === "RESOURCE" ? "SDK template" : "Saved configuration"}</small></div><button type="button" disabled={!connected || busy} onClick={() => void load(file.name).then(() => setView({ page: "editor" }))}>Edit</button></article>) : <p className="empty-state">{connected ? "No configurations found." : "Connect to retrieve configuration files."}</p>}
    </div>}

    {view.page === "editor" && config && <div className="configuration-editor-ui">
      <div className="configuration-breadcrumb"><button type="button" className="text-button" onClick={() => setView({ page: "list" })}>← Configurations</button><span>{saveName || "Untitled configuration"}</span></div>
      <label>Configuration name<input value={saveName} maxLength={60} disabled={busy} onChange={(event) => { setSaveName(event.target.value); setDirty(true); }} placeholder="Configuration name" /></label>
      <h3>USB devices and hub portals</h3>
      <div className="portal-list">{config.portals.map((item, portalIndex) => <article className="portal-card" key={`${item.serialNumber}-${portalIndex}`}><div><strong>{item.name}</strong><small>{item.serialNumber === "(embedded)" ? "Embedded Control Hub" : `Serial ${item.serialNumber || "unavailable"}`} · parent module {item.parentModuleAddress || "unavailable"}</small></div><div className="portal-hubs">{item.hubs.map((hub, hubIndex) => <button key={`${hub.address}-${hubIndex}`} type="button" onClick={() => setView({ page: "hub", portal: portalIndex, hub: hubIndex })}>{hub.name}<small>Module address {hub.address}</small></button>)}</div></article>)}</div>
      {config.webcams.length > 0 && <><h3>USB cameras</h3>{config.webcams.map((camera, index) => <label className="inline-field" key={camera.serialNumber || index}>Webcam ({camera.serialNumber})<input value={camera.name} onChange={(event) => update((next) => { next.webcams[index].name = event.target.value; return next; })} /></label>)}</>}
      <div className="configuration-footer"><p className="notice" aria-live="polite">{notice}</p><button className="danger" type="button" disabled={!canSave} onClick={() => void save()}>Save &amp; activate</button></div>
    </div>}

    {view.page === "hub" && current && portal && <div className="configuration-editor-ui">
      <div className="configuration-breadcrumb"><button type="button" className="text-button" onClick={() => setView({ page: "editor" })}>← {portal.name}</button><span>{current.name} · module address {current.address}</span></div>
      <h3>Configure {current.name}</h3><p className="empty-state">Choose a port group. A group’s Done button returns here without writing to the Robot Controller.</p>
      <div className="hub-groups">{GROUPS.map((item) => <button key={item.key} type="button" onClick={() => setView({ page: "group", portal: (view as Extract<View, { page: "hub" }>).portal, hub: (view as Extract<View, { page: "hub" }>).hub, group: item.key })}><strong>{item.label}</strong><small>{current.devices.filter((device) => device.group === item.key).length} configured</small></button>)}{[0, 1, 2, 3].map((bus) => <button key={`i2c-${bus}`} type="button" onClick={() => setView({ page: "group", portal: (view as Extract<View, { page: "hub" }>).portal, hub: (view as Extract<View, { page: "hub" }>).hub, group: "i2c", bus })}><strong>I2C Bus {bus}</strong><small>{current.devices.filter((device) => device.group === "i2c" && device.bus === bus).length} configured</small></button>)}</div>
    </div>}

    {view.page === "group" && current && groupInfo && <div className="configuration-editor-ui">
      <div className="configuration-breadcrumb"><button type="button" className="text-button" onClick={() => setView({ page: "hub", portal: view.portal, hub: view.hub })}>← {current.name}</button><span>{view.group === "i2c" ? `I2C Bus ${view.bus}` : groupInfo.label}</span></div>
      {view.group !== "i2c" && <div className="port-rows">{Array.from({ length: groupInfo.ports ?? 0 }, (_, portNumber) => {
        const deviceIndex = current.devices.findIndex((device) => device.group === view.group && device.port === portNumber);
        const device = deviceIndex < 0 ? undefined : current.devices[deviceIndex];
        return <div className="port-row" key={portNumber}><strong>Port {portNumber}</strong><select value={device?.tag ?? ""} onChange={(event) => update((next) => { const hub = next.portals[view.portal].hubs[view.hub]; const index = hub.devices.findIndex((entry) => entry.group === view.group && entry.port === portNumber); if (!event.target.value) { if (index >= 0) hub.devices.splice(index, 1); } else { const type = TYPES.find((entry) => entry.tag === event.target.value)!; const replacement = deviceFor(type, portNumber); if (index >= 0) hub.devices[index] = { ...replacement, name: hub.devices[index].name }; else hub.devices.push(replacement); } return next; })}><option value="">Nothing</option>{device && !TYPES.some((type) => type.tag === device.tag) && <option value={device.tag}>{device.label} (preserved)</option>}{groupTypes.map((type) => <option key={type.tag} value={type.tag}>{type.label}</option>)}</select><input value={device?.name ?? ""} disabled={!device} placeholder="Hardware-map name" onChange={(event) => update((next) => { const target = next.portals[view.portal].hubs[view.hub].devices.find((entry) => entry.group === view.group && entry.port === portNumber); if (target) target.name = event.target.value; return next; })} /></div>;
      })}</div>}
      {view.group === "i2c" && <div className="i2c-editor"><p className="empty-state">The built-in IMU is automatically retained when reported by the scanned configuration. I2C address is kept as local validation metadata; the Robot Controller XML uses the bus and connector position.</p>{i2cDevices.map((device, deviceIndex) => <div className="i2c-row" key={`${device.tag}-${deviceIndex}`}><select value={device.tag} disabled={device.source === "auto"} onChange={(event) => update((next) => { const target = next.portals[view.portal].hubs[view.hub].devices.filter((entry) => entry.group === "i2c" && entry.bus === view.bus)[deviceIndex]; const type = TYPES.find((entry) => entry.tag === event.target.value); if (target && type) { target.tag = type.tag; target.label = type.label; } return next; })}>{groupTypes.map((type) => <option key={type.tag} value={type.tag}>{type.label}</option>)}</select><input value={device.name} placeholder="Hardware-map name" onChange={(event) => update((next) => { const target = next.portals[view.portal].hubs[view.hub].devices.filter((entry) => entry.group === "i2c" && entry.bus === view.bus)[deviceIndex]; if (target) target.name = event.target.value; return next; })} /><input value={device.address ?? ""} placeholder="I2C address (optional)" onChange={(event) => update((next) => { const target = next.portals[view.portal].hubs[view.hub].devices.filter((entry) => entry.group === "i2c" && entry.bus === view.bus)[deviceIndex]; if (target) target.address = event.target.value; return next; })} /><button className="secondary" type="button" disabled={device.source === "auto"} onClick={() => update((next) => { const hub = next.portals[view.portal].hubs[view.hub]; const index = hub.devices.findIndex((entry) => entry.group === "i2c" && entry.bus === view.bus && entry.name === device.name && entry.tag === device.tag); if (index >= 0) hub.devices.splice(index, 1); return next; })}>Remove</button></div>)}<button className="secondary" type="button" onClick={() => update((next) => { next.portals[view.portal].hubs[view.hub].devices.push(deviceFor(TYPES.find((type) => type.tag === "RevColorSensorV3")!, view.bus === 0 ? 1 : 0, view.bus)); return next; })}>+ Add I2C device</button>{i2cDuplicateAddresses.length > 0 && <p className="validation-warning">Address collision on this I2C bus: {i2cDuplicateAddresses.join(", ")}</p>}</div>}
      <div className="configuration-footer"><p className="notice">Done keeps these edits local until Save &amp; activate.</p><button type="button" className="primary" onClick={() => setView({ page: "hub", portal: view.portal, hub: view.hub })}>Done</button></div>
    </div>}
    {view.page !== "list" && view.page !== "editor" && <p className="notice" aria-live="polite">{notice}</p>}
  </section>;
}
