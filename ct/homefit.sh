#!/usr/bin/env bash
# Copyright (c) 2026 HomeFit contributors
# License: MIT
# Source: https://github.com/abwalker417/HomeFit
#
# Community-Scripts-compatible entrypoint. This file is ready for a future
# contribution to community-scripts/ProxmoxVE. Until it is accepted there, use
# scripts/homefit-v2-lxc.sh from the HomeFit repository instead.
source <(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/misc/build.func)

APP="HomeFit"
var_tags="${var_tags:-fitness;health}"
var_disk="${var_disk:-12}"
var_cpu="${var_cpu:-2}"
var_ram="${var_ram:-2048}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_unprivileged="${var_unprivileged:-1}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources
  if [[ ! -x /usr/local/sbin/homefit-update || ! -L /opt/homefit/current ]]; then
    msg_error "No ${APP} installation was found."
    exit 1
  fi
  msg_info "Updating ${APP}"
  /usr/local/sbin/homefit-update
  msg_ok "Updated ${APP}"
  exit 0
}

start
build_container
description

msg_ok "Completed successfully!\n"
echo -e "${CREATING}${GN}${APP} has been installed.${CL}"
echo -e "${INFO}${YW}Open: ${GATEWAY}${BGN}http://${IP}:5000${CL}"
