# Garage HomeFit Kiosk — Pi 4 setup

Drop-in kit for driving the garage strip panel as a HomeFit workout kiosk.
Target: **Raspberry Pi 4 (4GB)** + Raspberry Pi OS **Lite (64-bit, Bookworm)**.

**Panel: GeeekPi EP-0189** — 8.8" letterbox LCD, **1920×480** (HSD088IPW1). It is
physically a 480×1920 *portrait* panel rotated 90° to landscape, which is why it
needs explicit timings + rotation rather than plug-and-play EDID.

The kiosk boots straight into Chromium fullscreen on
`http://192.168.68.15:5000/garage` — nothing else on screen. The garage page is
deliberately lean (no heavy app shell), so a Pi 4 runs it comfortably.

> **Cable note:** Pi 4 uses **micro-HDMI**. Use the port nearest the USB-C power
> (that's connector `HDMI-A-1`). Order a micro-HDMI→HDMI cable with the Pi.

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

## 2. The resolution (the fiddly part — but this panel is well-documented)

1920×480 is non-standard *and* the panel is internally 480×1920 rotated, so EDID
auto-detect won't do it. Use GeeekPi's own tool first; the manual line is the
fallback.

### A. GeeekPi's resolution tool (try this first)
GeeekPi ships a config script that knows the EP-0189's exact timing + rotation:

```bash
sudo apt install -y git
git clone https://github.com/geeekpi/lcd-config.git
cd lcd-config
sudo ./resolution_tool.sh    # pick the 8.8" 1920x480 panel, reboot
```

After reboot, confirm the mode and that a DRM device exists (cage needs it):

```bash
cat /sys/class/drm/card*-HDMI-A-1/modes   # expect 1920x480 (or 480x1920 pre-rotate)
ls /dev/dri/card*                          # KMS present → use cage (step 4)
```

### B. Manual config (fallback if the tool misbehaves)
Append the verbatim, community-proven timing to `/boot/firmware/config.txt` — it's
in [`config.txt.append`](./config.txt.append). The key lines:

```
hdmi_timings=480 1 48 32 80 1920 0 3 10 56 0 0 0 60 0 75840000 3
hdmi_group=2
hdmi_mode=87
hdmi_force_mode=1
hdmi_drive=1
config_hdmi_boost=4
max_framebuffer_height=1920
display_hdmi_rotate=1       # rotate the 480x1920 panel to 1920x480 landscape
```

> **Bookworm/KMS caveat:** `hdmi_timings` is a *legacy firmware* mechanism. It is
> honored by the firmware for the boot console, but the modern KMS driver
> (`vc4-kms-v3d`) may ignore it. If after the manual route the console shows
> 1920×480 but Wayland/cage comes up wrong, the cleanest fix is a **custom EDID**:
> `sudo edid-decode /sys/class/drm/card*-HDMI-A-1/edid`, rebuild a 480×1920 EDID
> (github.com/akatrevorjay/edid-generator), drop it at
> `/lib/firmware/edid/ep0189.bin`, and add
> `drm.edid_firmware=HDMI-A-1:edid/ep0189.bin` to `cmdline.txt`. **The GeeekPi
> tool (A) is preferred precisely because it handles this for you.**

> **Rotation:** mounted horizontally = landscape. The panel is native portrait,
> so the rotate flag above (or the GeeekPi tool) is what makes it read left-to-
> right. If text comes out upside down, use `display_hdmi_rotate=3`.

## 3. config.txt (kiosk bits)

Append the "kiosk bits" from [`config.txt.append`](./config.txt.append) to
`/boot/firmware/config.txt` (force-hotplug, no overscan, no blanking). If you went
the **manual** resolution route in 2B, that file also holds the panel timing block
— if you used the GeeekPi tool (2A), skip the `hdmi_timings` block.

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
- Finish → posts to HomeFit + Sparky, returns to picker.

---

## Notes
- **URL:** LAN IP `192.168.68.15:5000` is used directly (faster, skips the
  Cloudflare Zero Trust hop the external URL goes through). The garage routes are
  in HomeFit's `PUBLIC_ENDPOINTS`, so no login is needed on the panel.
- **Screen blanking:** `consoleblank=0` is set via config; Chromium kiosk keeps
  the display awake. If it ever blanks, that's the first thing to check.
- **Crash recovery:** the launcher wraps cage in a `while` loop, so a Chromium
  crash relaunches automatically.
- **Updates:** `--check-for-update-interval` is pinned far out so Chromium never
  nags mid-workout.
