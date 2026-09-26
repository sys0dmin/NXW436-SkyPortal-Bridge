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
✓ Controlled NXW436 AUX rate 0x02 manual movement
✓ Direction-aware same-prefix double STOP
○ physical coordinate calibration / pointing
○ direct-UART AZ/ALT encoder telemetry regression
○ tracking
○ ESP32 deployment
