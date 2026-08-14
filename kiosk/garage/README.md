# Garage BuiltHere Kiosk — Pi 4 setup

Drop-in kit for driving the garage strip panel as a BuiltHere workout kiosk.
Target: **Raspberry Pi 4 (4GB)** + Raspberry Pi OS **Lite (64-bit, Bookworm)**.

**Panel: GeeekPi EP-0189** — 11.26" IPS letterbox LCD, **1920×440**, glossy,
capacitive multi-touch (same strip as the desk panel). The Pi mounts on the
**back** of the panel via the GPIO pogo-pins + M2.5 screws; video is a short
micro-HDMI→HDMI jumper and touch is a USB-to-microUSB lead. Compatible with Pi
5 / 4B / 3B+ / 3B per GeeekPi.

The kiosk boots straight into Chromium fullscreen on
`http://192.168.68.15:5000/garage` — nothing else on screen. The garage page is
deliberately lean (no heavy app shell), so a Pi 4 runs it comfortably.

> **Cable note:** Pi 4 uses **micro-HDMI**. Use the port nearest the USB-C power
> (that's connector `HDMI-A-1`). The panel ships with the micro-HDMI jumper.

---

## 0. Flash the card

Raspberry Pi Imager → **Raspberry Pi OS Lite (64-bit)**. In the gear/⚙ options:
- hostname: `garage-kiosk`
- enable SSH, set user `garage` + password
- set Wi-Fi (or use ethernet)

Boot, `ssh garage@garage-kiosk.local`, then `sudo apt update && sudo apt full-upgrade -y`.

## 1. Install the kiosk software

```bash
sudo apt install -y cage chromium-browser edid-decode
```

`cage` is a single-app Wayland kiosk compositor — it runs Chromium fullscreen and
nothing else.

## 2. The resolution (the fiddly part)

1920×440 is a non-standard mode. **Read it off the panel first** — don't guess a
timing line, and note GeeekPi's `resolution_tool.sh` does NOT cover this panel
(it only handles their small 800×480 / 1024×600 touchscreens).

### A. Try EDID auto-detect first
Plug the panel into `HDMI-A-1`, boot, and see what the kernel reads from it:

```bash
cat /sys/class/drm/card*-HDMI-A-1/modes      # is 1920x440 listed?
sudo edid-decode /sys/class/drm/card*-HDMI-A-1/edid   # the panel's real timing
ls /dev/dri/card*                            # KMS present → cage works (step 4)
```

If `1920x440` shows up as the preferred mode, you're done — cage picks it up.
Skip to step 3.

### B. Force the mode (if EDID is wrong/absent)
Take the **exact** numbers from the `edid-decode` output above (detailed timings:
pixel clock + h/v front-porch/sync/back-porch) and build the `hdmi_timings` line
from them — see the Raspberry Pi config.txt docs for field order. Pair with:

```
hdmi_group=2
hdmi_mode=87
hdmi_force_mode=1
```

> Do NOT copy a timing line from another panel — the porches/clock differ per
> panel and a wrong line gives a black or garbled screen. The EP-0189 already
> works on Brent's gaming PC, so its EDID is good; reading it (A) is the reliable
> path. GeeekPi's product listing / the seller can also supply the official
> `config.txt` block for this model if EDID auto-detect fails.

> **Bookworm/KMS note:** `hdmi_timings` is a legacy-firmware mechanism that the
> modern KMS driver (`vc4-kms-v3d`) may ignore. If the console shows 1920×440 but
> Wayland/cage comes up wrong, force it with a **custom EDID** instead: dump +
> fix the panel EDID (github.com/akatrevorjay/edid-generator), drop it at
> `/lib/firmware/edid/ep0189.bin`, and add
> `drm.edid_firmware=HDMI-A-1:edid/ep0189.bin` to `cmdline.txt`.

> **Orientation:** the panel is a horizontal strip (1920 wide × 440 tall) — if it
> comes up sideways/upside-down, rotate with `display_hdmi_rotate=1` (or `3`).

## 3. config.txt (kiosk bits)

Append the "kiosk bits" from [`config.txt.append`](./config.txt.append) to
`/boot/firmware/config.txt` (force-hotplug, no overscan, no blanking). If EDID
auto-detect failed and you built an `hdmi_timings` line in 2B, add that here too.

## 4. Kiosk autostart

```bash
# launcher
sudo cp kiosk-launch.sh /usr/local/bin/garage-kiosk
sudo chmod +x /usr/local/bin/garage-kiosk

# console autologin for the garage user
sudo raspi-config nonint do_boot_behaviour B2

# launch cage on tty1 at login
cat bash_profile.snippet >> ~/.bash_profile
```

Reboot. It should come up in the garage picker, fullscreen, cursor hidden.

> `cage` needs a KMS DRM device (`/dev/dri/card*`). If the resolution route left
> the Pi on legacy firmware graphics (no `/dev/dri`), run the page under X11
> instead: `sudo apt install -y xserver-xorg xinit openbox` and swap the launcher
> to `startx /usr/bin/chromium-browser -- --kiosk --app=$URL` from `.bash_profile`.

## 5. Verify

- Tap a user → today's plan loads.
- Log a set → rest timer counts down.
- Play something on `media_player.garage` → the now-playing bar populates
  (title/artist/art) and the transport buttons control it.
- Finish → posts to BuiltHere + Sparky, returns to picker.

---

## Notes
- **URL:** LAN IP `192.168.68.15:5000` is used directly (faster, skips the
  Cloudflare Zero Trust hop the external URL goes through). The garage routes are
  in BuiltHere's `PUBLIC_ENDPOINTS`, so no login is needed on the panel.
- **Screen blanking:** `consoleblank=0` is set via config; Chromium kiosk keeps
  the display awake. If it ever blanks, that's the first thing to check.
- **Crash recovery:** the launcher wraps cage in a `while` loop, so a Chromium
  crash relaunches automatically.
- **Updates:** `--check-for-update-interval` is pinned far out so Chromium never
  nags mid-workout.
