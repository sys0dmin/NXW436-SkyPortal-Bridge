Celestron NXW436 SkyPortal Bridge

Reverse-engineered bridge allowing legacy Celestron NexStar GT
mounts using the NXW436 motor-control board to communicate with
modern Celestron clients such as SkyPortal/SkySafari.

SkyPortal
   ↓
HBG3-compatible Wi-Fi/AUX frontend
   ↓
AUX semantic translator
   ↓
MountController
   ↓
NXW436 legacy UART protocol
   ↓
NexStar GT mount
STATUS: EXPERIMENTAL / WORK IN PROGRESS

✓ NXW436 UART protocol reverse engineered
✓ AZ/ALT position reading
✓ manual motor control
✓ closed-loop GoTo
✓ HBG3-compatible Infrastructure discovery
✓ SkyPortal TCP/AUX connection confirmed
✓ MC_GET_VER
✓ MC_GET_MODEL
✓ SkyPortal startup handshake and control UI
✓ Experimental AUX manual movement via FakeMountBackend
✓ Real SkyPortal manual control on NXW436
✓ Real encoder position feedback during manual use
✓ SkyPortal crosshair follows physical mount movement
✓ AUX manual rates 0x02 / 0x05 / 0x07 / 0x09 mapped to discrete validated NXW436 payloads
✓ Current four-quadrant direct-UART validation for 0x09 / 00E5E3
✓ Direction-aware same-prefix double STOP
✓ Startup-idempotent STOP and best-effort post-STOP telemetry
○ physical coordinate calibration / pointing
○ SkyPortal GoTo + MC_SLEW_DONE
○ tracking
○ ESP32 deployment
