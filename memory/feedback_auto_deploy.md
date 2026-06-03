---
name: feedback-auto-deploy
description: After every code change, automatically commit, push to GitHub, and pull on the LXC — unless the change risks erasing user data
metadata:
  type: feedback
---

After making any code change, automatically: commit it, push to GitHub (origin develop), and pull + restart on the LXC (`root@192.168.68.15`).

**Why:** User wants changes live immediately without having to ask each time.

**How to apply:** Treat commit → push → LXC pull as the default end of every task. The one exception: if a change could destroy or migrate user data (workout history, weight tracking, profile data), stop and explain exactly what would be lost before proceeding. See [[feedback-live-db]] for the related database safety rule.
