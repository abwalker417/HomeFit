# Garage HomeFit Kiosk — Pi 4 setup

Drop-in kit for driving the 1920×440 garage strip panel as a HomeFit workout
kiosk. Target: **Raspberry Pi 4 (4GB)** + Raspberry Pi OS **Lite (64-bit, Bookworm)**.

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

## 2. The resolution (the only fiddly part)

1920×440 is a non-standard mode. Three outcomes, in order of likelihood:

### A. It just works (try this first)
Many strip panels advertise their native mode correctly over EDID. Plug the panel
into `HDMI-A-1`, boot, then check what the kernel sees:

```bash
cat /sys/class/drm/card*-HDMI-A-1/modes
```

If `1920x440` (or the panel's native, e.g. `1920x480`) is listed at the top →
you're done, skip to step 3. `cage` picks the preferred mode automatically.

### B. Force the mode via cmdline (quick try if A is wrong)
Add to the **end of the single line** in `/boot/firmware/cmdline.txt`:

```
video=HDMI-A-1:1920x440M@60
```

Reboot, re-check `modes`. The trailing `M` asks the kernel to generate a CVT
timing. If the panel accepts it, great. If it's blank or wrong, use C.

### C. Custom EDID override (the robust fix)
Tells the kernel the panel's exact native timing, so everything downstream
(cage, Chromium) runs at scale 1.

```bash
# Read what the panel currently reports
sudo edid-decode /sys/class/drm/card*-HDMI-A-1/edid

# Build a corrected EDID binary from the panel's real timings
#   - easiest: github.com/akatrevorjay/edid-generator  (edit Makefile timing, `make`)
#   - or fix the read-back EDID in wxEDID
# Place the result:
sudo mkdir -p /lib/firmware/edid
sudo cp garage-panel.bin /lib/firmware/edid/
```

Then add to `/boot/firmware/cmdline.txt`:

```
drm.edid_firmware=HDMI-A-1:edid/garage-panel.bin
```

Reboot. `modes` should now show the panel's native resolution as preferred.

> **Rotation:** the strip mounts horizontally (native landscape) — no rotation
> needed. If you ever mount it sideways, append `,rotate=90` to the `video=` line.

## 3. config.txt

Append the contents of [`config.txt.append`](./config.txt.append) to
`/boot/firmware/config.txt` (KMS driver, no overscan, force hotplug).

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
