# FTC Driver Station configuration UI → Robot Controller XML

## Purpose

This is a UI-first specification for translating a configuration made in the FTC Driver Station (DS) into the XML stored on the Robot Controller (RC). It is deliberately **not** an XML editor specification: the application should let a user reproduce the DS flow, construct a structured configuration model, and generate XML only when saving/applying it.

The XML is stored on the RC, even when it is created remotely using the paired DS. The DS guides the user from a USB/portal scan, through hubs and port groups, to a device type and a user-facing hardware name. [FIRST’s current getting-started guide](https://ftc-docs.firstinspires.org/en/latest/hardware_and_software_configuration/configuring/getting_started/getting-started.html) documents that workflow.

## Design principle

`Scan → portal → hub → port group/bus → device type → device name → Save/Activate`

That is the primary UI and data flow. XML import/export can be a later, advanced feature, but must not be the required way to configure hardware.

## DS-like navigation and controls

| DS-style location | User control / choice | Structured result | XML effect |
| --- | --- | --- | --- |
| Driver Station overflow menu | **Configure Robot** | Opens configuration list and retrieves the RC’s configurations | None |
| Configuration list | Choose an existing file; **Edit** | Load its configuration tree | None until Save |
| Configuration list | **New** | Create an unsaved configuration, then run hardware discovery | Root `Robot` tree is started |
| Configuration list | **Activate** | Mark a saved configuration as active on the RC | Active-config selection; not a change to the XML contents |
| Configuration list | Delete / rename (if provided) | Manage configuration-file metadata | File operation, not a device mapping |
| Top-level editor | **Scan** | Refresh detected USB devices / portals and preserve user edits where safely possible | Creates or refreshes `LynxUsbDevice` and `Webcam` candidates from discovery |
| USB device list | Tap a Portal | Open the hub chain behind the USB/embedded connection | Selects a `LynxUsbDevice` subtree |
| Portal screen | Tap a Control/Expansion Hub | Open that hub’s port groups | Selects a `LynxModule` subtree |
| Hub screen | Tap **Motors**, **Servos**, **Digital Devices**, **PWM Devices**, **Analog Input Devices**, or an **I2C Bus** | Open the corresponding physical-port editor | Selects children of the hub |
| Port editor | Per-port type dropdown (including **Nothing**) | Add, replace, or remove a typed device at the selected port | Add / replace / remove the corresponding child element |
| Port editor | Device-name text field | Set the SDK hardware-map name used in Java/Blocks | `name="…"` |
| I2C bus editor | **Add**, type dropdown, device name, remove | Add a typed I2C device to a specific hub bus | `port="…" bus="…"` on the I2C child |
| Any editor | **Done** | Commit screen-local edits and navigate up | No persistence on its own |
| Top-level editor | **Save**, configuration-name entry, **OK** | Validate, serialize, save to RC; optionally activate as DS does | Writes the configuration XML |
| Any unsaved editor | **Cancel** / Back | Discard or confirm discard | No XML change |

The app should display the active configuration and an unsaved/dirty indicator just as the DS flow does. **Save** is a separate action from navigating back with **Done**; do not silently save port changes.

## Configuration tree

The XML mirrors a tree, not a flat list:

```text
Robot
├── LynxUsbDevice                 (a Control Hub’s embedded portal, or a USB-connected hub portal)
│   ├── LynxModule                (Control Hub or Expansion Hub, identified by module address)
│   │   ├── motors                (four possible motor ports)
│   │   ├── servos                (six possible servo ports)
│   │   ├── digital I/O
│   │   ├── PWM outputs
│   │   ├── analog inputs
│   │   └── I2C devices           (attached to one of four I2C buses)
│   └── LynxModule                (additional RS-485 Expansion Hub, if present)
└── Webcam                        (USB camera, if configured)
```

The root is normally:

```xml
<Robot type="FirstInspires-FTC">
  <!-- scanned USB devices and their configured children -->
</Robot>
```

`LynxUsbDevice` describes the discovered transport/portal. A `LynxModule` describes a hub on that portal. The module’s `port` attribute is its **Lynx module address**, not a motor/servo socket number.

## Port-group mapping

Use the following mapping when the user selects a port type and enters a name. A choice of **Nothing** removes that device entry. Unconfigured physical sockets are normally absent from XML.

| DS hub screen | Physical ports | UI choice | XML child shape | Notes |
| --- | --- | --- | --- | --- |
| Motors | 0–3 | Motor type from dropdown + name | `<xmlTag name="name" port="0" />` | `xmlTag` is the selected SDK motor type, for example `goBILDA5202SeriesMotor` or `RevRoboticsUltraplanetaryHDHexMotor`. |
| Servos | 0–5 | Servo / Continuous Rotation Servo / compatible controller + name | `<Servo … />`, `<ContinuousRotationServo … />`, etc. | A REV SPARKmini is configured from this group, but its XML tag is `RevSPARKMini`. |
| Digital Devices | hub digital channel | Digital type + name | `<DigitalDevice … />`, `<RevTouchSensor … />`, etc. | A REV Touch Sensor normally uses the signal channel for its connector; FIRST’s tutorial example uses port 1. |
| PWM Devices | PWM channel | PWM device type + name | `<xmlTag name="name" port="n" />` | Keep this a catalog-driven group; device availability varies by SDK version. |
| Analog Input Devices | analog channel | Analog device type + name | `<AnalogInput … />` or typed analog sensor tag | Keep device type and port separate in the model. |
| I2C Bus 0–3 | bus plus device position/address | **Add**, I2C type + name | `<xmlTag name="name" port="p" bus="b" />` | Multiple configured I2C devices can share a bus only if their I2C addresses are compatible. The UI must retain device identity/address metadata even when XML itself has only `port`/`bus`. |

For the normal REV Hub screen, present the standard groups visible in the DS: Motors, Servos, Digital Devices, PWM Devices, Analog Input Devices, and I2C Bus 0–3. FIRST’s current guide explicitly shows the hub screen and the motor/servo/I2C editing sequences; it documents four I2C buses and the automatic internal IMU configuration on bus 0. [Motor/servo/configuration workflow](https://github.com/FIRST-Tech-Challenge/FtcRobotController/wiki/Configuring-Your-Hardware) · [Color-distance sensor example](https://ftc-docs.firstinspires.org/en/latest/hardware_and_software_configuration/configuring/configuring_color_sensor/configuring-color-sensor.html).

## Device-type dropdown → XML tag catalog

The UI should be fed by a versioned **device type registry**, not a hard-coded list embedded in the layout. Each entry needs at least:

```ts
type DeviceType = {
  displayName: string;      // e.g. "REV Color Sensor V3"
  xmlTag: string;           // e.g. "RevColorSensorV3"
  group: "motor" | "servo" | "digital" | "pwm" | "analog" | "i2c";
  compatibleWith: "REV_HUB"[];
  requiredAttributes: ("name" | "port" | "bus")[];
};
```

Useful built-in mappings verified against FTC SDK metadata and real configuration examples are:

| DS dropdown label | Group | XML tag |
| --- | --- | --- |
| Unspecified Motor | Motors | `Motor` |
| goBILDA 5201 Series | Motors | `goBILDA5201SeriesMotor` |
| goBILDA 5202 Series | Motors | `goBILDA5202SeriesMotor` |
| REV Robotics Core Hex Motor | Motors | `RevRoboticsCoreHexMotor` |
| REV Robotics 20:1 / 40:1 HD Hex Motor | Motors | `RevRobotics20HDHexMotor` / `RevRobotics40HDHexMotor` |
| REV Robotics UltraPlanetary HD Hex Motor | Motors | `RevRoboticsUltraplanetaryHDHexMotor` |
| Tetrix Motor | Motors | `TetrixMotor` |
| NeveRest 3.7 v1 / 20 / 40 / 60 | Motors | `NeveRest3.7v1Gearmotor`, `NeveRest20Gearmotor`, `NeveRest40Gearmotor`, `NeveRest60Gearmotor` |
| Servo | Servos | `Servo` |
| Continuous Rotation Servo | Servos | `ContinuousRotationServo` |
| REV SPARKmini Controller | Servos | `RevSPARKMini` |
| REV Blinkin LED Driver | Servos | `RevBlinkinLedDriver` |
| Digital Device | Digital | `DigitalDevice` |
| REV Touch Sensor | Digital | `RevTouchSensor` |
| Analog Input | Analog | `AnalogInput` |
| REV Color/Range Sensor (v1/v2) | I2C | `LynxColorSensor` |
| REV Color Sensor V3 | I2C | `RevColorSensorV3` |
| REV 2M Distance Sensor | I2C | `REV_VL53L0X_RANGE_SENSOR` |
| REV Blinkin LED Driver | I2C/servo-compatible catalogue entry depends on SDK UI | `RevBlinkinLedDriver` |
| Embedded Hub IMU | I2C | `LynxEmbeddedIMU` |

The exact dropdown contents are SDK-release dependent. The supported source of truth is the target RC SDK’s device-type metadata (`xmlTag`), rather than a product-name lookup. For example, FTC SDK documentation exposes `xmlTag="RevColorSensorV3"` for the V3 sensor, `xmlTag="REV_VL53L0X_RANGE_SENSOR"` for the REV 2M sensor, and `xmlTag="LynxEmbeddedIMU"` for the legacy embedded IMU. [V3](https://first-tech-challenge.github.io/SkyStone/com/qualcomm/hardware/rev/RevColorSensorV3.html) · [2M](https://javadoc.io/static/org.firstinspires.ftc/Hardware/10.1.0/com/qualcomm/hardware/rev/Rev2mDistanceSensor.html) · [embedded IMU](https://javadoc.io/static/org.firstinspires.ftc/Hardware/10.1.0/com/qualcomm/hardware/lynx/LynxEmbeddedIMU.html).

## Scan-owned values — never invent these

Some values are not meaningful UI choices. Read them from the RC scan result, preserve them through edits, and write them back unchanged:

| XML field | Source | Implementation rule |
| --- | --- | --- |
| `LynxUsbDevice@serialNumber` | USB/embedded discovery | Use `(embedded)` for a Control Hub; use the discovered serial number for a phone-connected Expansion Hub portal. |
| `LynxUsbDevice@parentModuleAddress` | Hub topology scan | Do not hard-code it. The scanner determines which module is the parent. |
| `LynxModule@port` | Hub/module address scan | Do not treat this as a UI port number. It is the module’s Lynx address. |
| `Webcam@serialNumber` | USB-camera scan | Preserve the discovered serial number; a user should not be asked to type it. |
| Internal IMU model/tag | Hub hardware/SDK scan | The automatically inserted IMU must match the actual hub/SDK. Do not always emit `LynxEmbeddedIMU`. |

This matters especially for a Control Hub plus Expansion Hub. A recent scanned configuration often has the embedded Control Hub module at address `173` and a chained Expansion Hub at another address, but those addresses are hardware configuration, not constants for application code. The UI should show a friendly hub label and the address only as supporting information.

## I2C special case: the built-in IMU

The DS automatically configures the hub IMU on I2C Bus 0. FIRST documents that it is internally connected to bus 0 and that users press **Add** when they want an additional device on that bus. The usual serialized form is:

```xml
<LynxEmbeddedIMU name="imu" port="0" bus="0" />
```

For physical I2C connectors, the XML’s `bus` is the connector label (0–3). The `port` distinguishes the internal versus external placement. A common convention is external Bus 0 as `port="1" bus="0"`, and external Buses 1–3 as `port="0" bus="1"` through `bus="3"`. Treat this as a serialization rule validated against the target SDK, not a number users should need to understand.

## Example: what the UI generates

Assume the user scans one Control Hub, opens it, configures:

- Motors: ports 0–3 as four goBILDA 5202 motors named `frontLeft`, `frontRight`, `backLeft`, `backRight`.
- Servos: port 0 as `Servo`, named `claw`.
- I2C Bus 0: retains the scanned embedded IMU named `imu`.
- I2C Bus 1: adds a `REV Color Sensor V3`, named `color`.
- USB devices: adds the scanned webcam, renamed `Webcam 1`.

The saved XML shape is:

```xml
<?xml version="1.0" encoding="UTF-8" standalone="yes" ?>
<Robot type="FirstInspires-FTC">
  <LynxUsbDevice name="Control Hub Portal"
                 serialNumber="(embedded)"
                 parentModuleAddress="173">
    <LynxModule name="Control Hub" port="173">
      <goBILDA5202SeriesMotor name="frontLeft"  port="0" />
      <goBILDA5202SeriesMotor name="frontRight" port="1" />
      <goBILDA5202SeriesMotor name="backLeft"   port="2" />
      <goBILDA5202SeriesMotor name="backRight"  port="3" />
      <Servo name="claw" port="0" />
      <LynxEmbeddedIMU name="imu" port="0" bus="0" />
      <RevColorSensorV3 name="color" port="0" bus="1" />
    </LynxModule>
  </LynxUsbDevice>
  <Webcam name="Webcam 1" serialNumber="SCANNED_CAMERA_SERIAL" />
</Robot>
```

`173` and `SCANNED_CAMERA_SERIAL` above are examples only. A production generator must use values from its scan session. A real two-hub configuration nests both `LynxModule` elements in the same portal, with each module’s scanned address.

## Validation before Save

- Require a non-empty, unique hardware-map name for every enabled device.
- Validate names against the target SDK’s accepted configuration-name rules; also warn when a changed name no longer matches a Blocks/Java reference.
- Enforce the port range and one configured device per non-I2C physical port.
- Do not allow incompatible type/group combinations (for example, a motor in a servo slot).
- Preserve the scan-owned portal serial numbers and module addresses.
- Maintain one compatible I2C configuration per physical device/address; warn about address collisions on the same bus.
- Retain the automatically discovered IMU unless the scan/SDK says otherwise.
- Validate XML against the target RC SDK by round-tripping it through the same configuration parser before offering **Apply**.

## Recommended internal model

Build the UI around an object model such as this, then serialize it:

```ts
type RobotConfig = {
  displayName: string;
  portals: PortalConfig[];
  usbDevices: WebcamConfig[];
};

type PortalConfig = {
  displayName: string;
  serialNumber: string;             // scan-owned
  parentModuleAddress: number;      // scan-owned
  modules: HubConfig[];
};

type HubConfig = {
  displayName: string;
  moduleAddress: number;            // scan-owned; maps to LynxModule@port
  devices: ConfiguredDevice[];
};

type ConfiguredDevice = {
  type: DeviceType;                 // determines XML element/tag
  name: string;                     // user-entered HardwareMap name
  port: number;
  bus?: number;                     // only for I2C
  source: "auto" | "user";         // preserve automatic IMU
};
```

This allows a future app to behave like the DS while safely generating and consuming the same configuration XML.

## Sources consulted

- [FIRST: Creating a configuration file with the Driver Station](https://ftc-docs.firstinspires.org/en/latest/hardware_and_software_configuration/configuring/getting_started/getting-started.html)
- [FIRST: Configuring a servo](https://ftc-docs.firstinspires.org/en/latest/hardware_and_software_configuration/configuring/configuring_servo/configuring-servo.html)
- [FIRST: Configuring a color-distance sensor and the automatic IMU](https://ftc-docs.firstinspires.org/en/latest/hardware_and_software_configuration/configuring/configuring_color_sensor/configuring-color-sensor.html)
- [FTC SDK wiki: configuration, digital touch, save and activate workflow](https://github.com/FIRST-Tech-Challenge/FtcRobotController/wiki/Configuring-Your-Hardware)
- [FTC SDK API: `LynxModuleConfiguration`](https://javadoc.io/static/org.firstinspires.ftc/RobotCore/7.0.0/com/qualcomm/robotcore/hardware/configuration/LynxModuleConfiguration.html)
- [FTC #9929’s extracted hardware-map template](https://ftc9929.com/2019/12/16/stress-free-ftc-hardware-configurations/) — useful implementation corroboration, but secondary to FIRST/SDK sources.

