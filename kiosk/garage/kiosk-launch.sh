#!/usr/bin/env bash
# Garage HomeFit kiosk launcher.
# Runs Chromium fullscreen under cage (single-app Wayland compositor).
# Installed to /usr/local/bin/garage-kiosk and exec'd from ~/.bash_profile on tty1.

URL="http://192.168.68.15:5000/garage"

# Relaunch if Chromium/cage ever exits (crash, OOM, etc.)
while true; do
  cage -- chromium-browser \
    --kiosk \
    --app="$URL" \
    --ozone-platform=wayland \
    --start-fullscreen \
    --incognito \
    --touch-events=enabled \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=Translate \
    --check-for-update-interval=31536000 \
    --overscroll-history-navigation=0
  sleep 2
done
