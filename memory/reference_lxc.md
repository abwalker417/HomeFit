---
name: reference-lxc
description: SSH address and update procedure for the HomeFit LXC container
metadata:
  type: reference
---

HomeFit runs in an LXC on Proxmox, accessible via SSH as `root@192.168.68.15`.

To update after pushing to the tracked branch:
```
ssh root@192.168.68.15 "cd /home/homefit/workout-app && git pull && .venv/bin/pip install -r requirements.txt && systemctl restart homefit"
```
