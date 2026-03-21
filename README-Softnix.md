# Softnix Admin Guide

คู่มือนี้อธิบายวิธีใช้งาน `Softnix Admin` สำหรับจัดการ `nanobot` หลาย instance แบบเป็นระเบียบ รองรับหลายผู้ใช้ และดูแลรักษาง่าย

## แนวคิด

`Softnix Admin` เป็น control plane สำหรับ `nanobot` โดยแยกโครงสร้างออกเป็น 2 ส่วน

- `~/.softnix/admin`
  ใช้เก็บ registry กลาง, audit, backups
- `~/.softnix/instances/<instance-id>`
  ใช้เก็บ config, workspace, logs, pid, scripts ของแต่ละ instance

1 instance ควรแทน 1 tenant, 1 user group หรือ 1 environment เช่น `acme-prod`, `acme-uat`, `team-a-dev`

## โครงสร้างไฟล์ที่แนะนำ

```text
~/.softnix/
  admin/
    instances.json
    backups/
    audit/

  instances/
    default-prod/
      instance.json
      config.json
      workspace/
        sessions/
        cron/
        HEARTBEAT.md
        AGENTS.md
        memory/
      run/
        gateway.pid
      logs/
        gateway.log
        gateway.err.log
      scripts/
        start.sh
        stop.sh
        restart.sh
        status.sh
```

## คำสั่งหลัก

มี 2 คำสั่งหลักที่ใช้กับ Softnix

```bash
/opt/anaconda3/bin/nanobot softnix-init
/opt/anaconda3/bin/nanobot softnix-admin
```

และ runtime ของแต่ละ instance จะรันผ่าน

```bash
/opt/anaconda3/bin/nanobot gateway
```

## Runtime Matrix

Softnix รองรับการทำงานของ instance ตาม matrix นี้

| `runtime.mode` | `runtime.sandbox.executionStrategy` | พฤติกรรม | เหมาะกับงานแบบไหน | สถานะ |
| --- | --- | --- | --- | --- |
| `host` | `persistent` | gateway และ tool ทั้งหมดรันบน host โดยตรง | compatibility mode, dev, debug | ใช้งานได้ |
| `sandbox` | `persistent` | gateway รันค้างใน Docker 1 container ต่อ 1 instance | online workload ทั่วไป, Telegram, MCP, provider API | ใช้งานได้ |
| `host` | `tool_ephemeral` | gateway/control plane รันบน host, ถ้างานไม่ใช้ tools จะตอบ inline, ถ้ามี tool call จะสร้าง ephemeral sandbox 1 งานต่อ 1 container แล้วลบทิ้ง | enterprise mode ที่ต้องการ balance ระหว่าง UX และ isolation | ใช้งานได้ |
| `sandbox` | `tool_ephemeral` | gateway อยู่ใน sandbox อยู่แล้ว แต่ strategy นี้ออกแบบให้ host เป็นคน launch one-off sandbox jobs | ไม่แนะนำในโค้ดปัจจุบัน | ยังไม่ควรใช้ |

### คำแนะนำในการเลือกโหมด

- ถ้าต้องใช้ `Telegram`, `MCP`, หรือ provider/cloud API ภายนอกเป็นหลัก:
  ใช้ `sandbox + persistent`
- ถ้าต้องการให้คำถามทั่วไปตอบเร็ว แต่เมื่อมี `shell / file / install / MCP / external actions` ค่อยแยกไป sandbox ชั่วคราว:
  ใช้ `host + tool_ephemeral`

## Sandbox Profiles

Admin UI และ instance config รองรับ `runtime.sandbox.profile` เพื่อเลือก preset สำหรับทีม operation ได้เร็วขึ้น

| Profile | ค่า runtime หลัก | แนวทางใช้งาน |
| --- | --- | --- |
| `strict` | `sandbox + persistent`, `networkPolicy=none`, `cpu=1`, `memory=1g`, `pids=128`, `tmpfs=64`, `timeout=60` | งาน offline หรือ workload ที่ต้องเน้น isolation สูงสุด |
| `balanced` | `sandbox + persistent`, `networkPolicy=default`, `cpu=2`, `memory=2g`, `pids=256`, `tmpfs=128`, `timeout=90` | production workload ทั่วไปที่ต้องคุยกับ Telegram, MCP, provider APIs |
| `fast` | `host + tool_ephemeral`, `networkPolicy=default`, `pids=512`, `tmpfs=256`, `timeout=180` | งานที่ต้องการ flexibility สูง, ตอบคำถามทั่วไปเร็ว, และค่อยเปิด sandbox เมื่อมี tool call |

หมายเหตุ:
- profile จะเติมค่า default ให้ทันทีในหน้า Admin UI
- ถ้าใช้ Advanced mode ยังสามารถ override ค่าเฉพาะจุดได้หลังเลือก profile
- ถ้าต้องการ compatibility กับ flow เดิม:
  ใช้ `host + persistent`

### ตัวอย่างการคิดแบบง่าย

- ถามว่า `สวัสดีครับ` หรือ `ช่วยร่างอีเมลให้หน่อย`
  ในโหมด `host + tool_ephemeral` จะตอบทันทีโดยไม่สร้าง container
- ถามว่า `ช่วยอ่านไฟล์`, `ช่วยรัน shell`, `ช่วยติดตั้ง package`, `ช่วยใช้ MCP`
  ในโหมด `host + tool_ephemeral` ระบบจะสร้าง ephemeral sandbox เฉพาะงานนั้น แล้วลบทิ้งเมื่อจบ

## 1. สร้าง instance แรก

ตัวอย่างสร้าง instance ชื่อ `default-prod` โดยใช้ config เดิมจาก `~/.nanobot/config.json`

```bash
/opt/anaconda3/bin/nanobot softnix-init \
  --instance-id default-prod \
  --name "Default Production" \
  --owner default \
  --env prod \
  --source-config ~/.nanobot/config.json \
  --repo-root /Volumes/Seagate/myapp/nanobot
```

ผลลัพธ์ที่ได้:

- สร้างโครงสร้างภายใต้ `~/.softnix/instances/default-prod`
- สร้าง `config.json` ของ instance
- สร้าง `workspace/`, `logs/`, `run/`, `scripts/`
- ลงทะเบียน instance นี้ไว้ใน `~/.softnix/admin/instances.json`

## 2. เปิด Softnix Admin

ถ้ามีไฟล์ `~/.softnix/admin/instances.json` อยู่แล้ว สามารถเปิด admin ได้เลย

```bash
/opt/anaconda3/bin/nanobot softnix-admin
```

หรือระบุ registry ตรง ๆ

```bash
/opt/anaconda3/bin/nanobot softnix-admin --registry ~/.softnix/admin/instances.json
```

จากนั้นเปิดเบราว์เซอร์ที่

[http://127.0.0.1:18880](http://127.0.0.1:18880)

## 3. Start / Stop / Restart / Status ของ instance

แต่ละ instance จะมี scripts ของตัวเองอยู่ที่

```text
~/.softnix/instances/<instance-id>/scripts/
```

ตัวอย่าง:

```bash
~/.softnix/instances/default-prod/scripts/start.sh
~/.softnix/instances/default-prod/scripts/stop.sh
~/.softnix/instances/default-prod/scripts/restart.sh
~/.softnix/instances/default-prod/scripts/status.sh
```

scripts เหล่านี้ทำงานดังนี้

- `start.sh`
  รัน `/opt/anaconda3/bin/nanobot gateway --config ... --workspace ...` แบบ background
- `stop.sh`
  หยุด process จาก `run/gateway.pid`
- `restart.sh`
  stop แล้ว start ใหม่
- `status.sh`
  ตรวจ PID แล้วคืน `running` หรือ `stopped`

หน้า `Instances` ใน admin UI ใช้ scripts เหล่านี้โดยตรงผ่าน registry

## 4. ตัวอย่าง registry

ไฟล์ registry กลางอยู่ที่

[instances.json](/Users/rujirapongair/.softnix/admin/instances.json)

ตัวอย่าง:

```json
{
  "instances": [
    {
      "id": "default-prod",
      "name": "Default Production",
      "owner": "default",
      "env": "prod",
      "instance_home": "/Users/rujirapongair/.softnix/instances/default-prod",
      "config": "/Users/rujirapongair/.softnix/instances/default-prod/config.json",
      "workspace": "/Users/rujirapongair/.softnix/instances/default-prod/workspace",
      "cwd": "/Volumes/Seagate/myapp/nanobot",
      "lifecycle": {
        "start": ["/Users/rujirapongair/.softnix/instances/default-prod/scripts/start.sh"],
        "stop": ["/Users/rujirapongair/.softnix/instances/default-prod/scripts/stop.sh"],
        "restart": ["/Users/rujirapongair/.softnix/instances/default-prod/scripts/restart.sh"],
        "status": ["/Users/rujirapongair/.softnix/instances/default-prod/scripts/status.sh"]
      }
    }
  ]
}
```

## 5. เพิ่ม instance สำหรับผู้ใช้หลายคน

ตัวอย่าง:

```bash
/opt/anaconda3/bin/nanobot softnix-init \
  --instance-id acme-prod \
  --name "Acme Production" \
  --owner acme \
  --env prod \
  --source-config ~/.nanobot/config.json \
  --repo-root /Volumes/Seagate/myapp/nanobot
```

```bash
/opt/anaconda3/bin/nanobot softnix-init \
  --instance-id acme-uat \
  --name "Acme UAT" \
  --owner acme \
  --env uat \
  --source-config ~/.nanobot/config.json \
  --repo-root /Volumes/Seagate/myapp/nanobot
```

ทุกครั้งที่สร้าง instance ใหม่ ระบบจะ update `~/.softnix/admin/instances.json` ให้

## 6. สิ่งที่ควรปรับหลัง bootstrap

หลังจากสร้าง instance แล้ว ควรเข้าไปแก้ไฟล์เหล่านี้

- `~/.softnix/instances/<instance-id>/config.json`
  ตั้งค่า model, providers, channels, security
- `~/.softnix/instances/<instance-id>/instance.json`
  ตั้ง metadata เช่น description, tags

## 7. แนวทางดูแลรักษา

- ใช้ 1 instance ต่อ 1 user group หรือ 1 environment
- อย่าแชร์ `workspace/` ข้าม instance
- เก็บ logs, pid, cron, sessions แยกต่อ instance เสมอ
- ใช้ naming pattern เดียว เช่น `<owner>-<env>`
- backup ทั้ง directory ของ instance ได้ทันที

## 8. ปัญหาที่พบบ่อย

### หน้า Instances กด Start/Stop ไม่ได้

ตรวจว่า registry มี `lifecycle.start`, `lifecycle.stop`, `lifecycle.status` ครบหรือไม่

### หน้า Instances ไม่เห็น Running/Stopped

ตรวจว่า `status.sh` ทำงานได้จริง

```bash
~/.softnix/instances/default-prod/scripts/status.sh
```

ควรคืน:

- `running` และ exit code `0`
- หรือ `stopped` และ exit code `1`

### เปิด admin แล้วเห็นแค่ instance เดียว

ตรวจว่าเปิดด้วย registry ที่ถูกต้อง

```bash
/opt/anaconda3/bin/nanobot softnix-admin --registry ~/.softnix/admin/instances.json
```

## 9. สรุป flow ที่แนะนำ

1. สร้าง instance ด้วย `softnix-init`
2. ปรับ config ของ instance
3. ใช้ `scripts/start.sh` เพื่อเปิด gateway
4. เปิด `softnix-admin`
5. บริหาร instance ทั้งหมดจากหน้า UI กลาง

---

## 10. Role-Based Access Control (RBAC)

ระบบใช้ 4 roles เรียงลำดับจากสิทธิ์น้อยไปมาก: **viewer → operator → admin → owner**

ใน UI บางจุดจะแสดง `Owner System` แทน `owner` เพื่อให้อ่านง่ายขึ้น แต่ค่าจริงในระบบยังเป็น `owner`

ตัวย่อ: `✅` = มีสิทธิ์ · `❌` = ไม่มีสิทธิ์

### 10.1 Permission Matrix

| Module / Function | Permission Key | viewer | operator | admin | owner |
|---|---|:---:|:---:|:---:|:---:|
| **Dashboard & Monitoring** |||||
| ดู Overview / Health | `overview.read` | ✅ | ✅ | ✅ | ✅ |
| ดู Activity Log | `activity.read` | ✅ | ✅ | ✅ | ✅ |
| ดู Runtime Audit Log | `runtime_audit.read` | ✅ | ✅ | ✅ | ✅ |
| ดู Security Audit / Auth Log | `security.read` | ✅ | ✅ | ✅ | ✅ |
| แก้ไข Security / Guardrails | `security.update` | ❌ | ❌ | ❌ | ✅ |
| **Instance Management** |||||
| ดูรายการ / รายละเอียด instance | `instance.read` | ✅ | ✅ | ✅ | ✅ |
| สร้าง instance | `instance.create` | ❌ | ❌ | ❌ | ✅ |
| แก้ไข instance (profile, env, gateway port) | `instance.update` | ❌ | ❌ | ✅ | ✅ |
| ลบ instance | `instance.delete` | ❌ | ❌ | ❌ | ✅ |
| Start / Stop / Restart instance | `instance.control` | ❌ | ✅ | ✅ | ✅ |
| **Configuration** |||||
| ดู config ของ instance | `config.read` | ✅ | ✅ | ✅ | ✅ |
| แก้ไข config (runtime, sandbox) | `config.update` | ❌ | ❌ | ✅ | ✅ |
| เปิด/ปิด Workspace Restriction | `config.update` | ❌ | ❌ | ✅ | ✅ |
| **Memory Files** |||||
| ดู Memory / Prompt files | `memory.read` | ✅ | ✅ | ✅ | ✅ |
| แก้ไข Memory / Prompt files | `memory.update` | ❌ | ❌ | ✅ | ✅ |
| **Skills** |||||
| ดู Skills | `skills.read` | ✅ | ✅ | ✅ | ✅ |
| แก้ไข Skill files | `skills.update` | ❌ | ❌ | ✅ | ✅ |
| ลบ Skill | `skills.delete` | ❌ | ❌ | ✅ | ✅ |
| **Channels (Telegram, LINE, WhatsApp ฯลฯ)** |||||
| ดู Channel config | `channel.read` | ✅ | ✅ | ✅ | ✅ |
| แก้ไข Channel (enable/disable, allow_from) | `channel.update` | ❌ | ❌ | ✅ | ✅ |
| **LLM Providers** |||||
| ดู Provider config | `provider.read` | ✅ | ✅ | ✅ | ✅ |
| แก้ไข Provider (api_key, model, api_base) | `provider.update` | ❌ | ❌ | ✅ | ✅ |
| **MCP Servers** |||||
| ดู MCP Server config | `mcp.read` | ✅ | ✅ | ✅ | ✅ |
| เพิ่ม / แก้ไข / ลบ MCP Server | `mcp.update` | ❌ | ❌ | ✅ | ✅ |
| **Schedules / Cron** |||||
| ดูรายการ Schedule | `schedule.read` | ✅ | ✅ | ✅ | ✅ |
| รัน Schedule ทันที (manual run) | `schedule.run` | ❌ | ✅ | ✅ | ✅ |
| สร้าง / แก้ไข / เปิดปิด / ลบ Schedule | `schedule.update` | ❌ | ❌ | ✅ | ✅ |
| **Access Requests** |||||
| อนุมัติ / ปฏิเสธ Access Request | `access_request.review` | ❌ | ✅ | ✅ | ✅ |
| **User Management** |||||
| ดูรายชื่อ Users | `user.read` | ❌ | ❌ | ✅ | ✅ |
| สร้าง User (role ≠ owner) | `user.create` | ❌ | ❌ | ✅ | ✅ |
| แก้ไข User (role ≠ owner) | `user.update` | ❌ | ❌ | ✅ | ✅ |
| Reset password User อื่น | `user.update` | ❌ | ❌ | ✅ | ✅ |
| Disable / Enable User | `user.disable` | ❌ | ❌ | ❌ | ✅ |
| สร้าง / แก้ไข / เลื่อน User ที่เป็น owner | `auth.manage` | ❌ | ❌ | ❌ | ✅ |
| **Account (ตัวเอง)** |||||
| เปลี่ยน password ตัวเอง | `__self__` | ✅ | ✅ | ✅ | ✅ |

---

### 10.2 สรุปสิทธิ์รายมิติ

| มิติ | viewer | operator | admin | owner |
|---|---|---|---|---|
| **อ่านข้อมูลใน scope ของตัวเอง** | ✅ | ✅ | ✅ | ✅ |
| **ควบคุม instance** (start/stop) | ❌ | ✅ | ✅ | ✅ |
| **อนุมัติ Access Request** | ❌ | ✅ | ✅ | ✅ |
| **รัน Schedule ทันที** | ❌ | ✅ | ✅ | ✅ |
| **แก้ไข config / channels / providers** | ❌ | ❌ | ✅ | ✅ |
| **จัดการ instance** (สร้าง/แก้ไข) | ❌ | ❌ | ❌ | ✅ |
| **จัดการ Users** (ยกเว้น owner) | ❌ | ❌ | ✅ | ✅ |
| **ลบ instance** | ❌ | ❌ | ❌ | ✅ |
| **Disable User** | ❌ | ❌ | ❌ | ✅ |
| **จัดการ User ที่เป็น owner** | ❌ | ❌ | ❌ | ✅ |

---

### 10.3 Instance Scope

ผู้ใช้แต่ละคนสามารถมี `instance_ids` กำกับไว้ในโปรไฟล์ได้ เมื่อเป็น non-owner:

- หน้า Overview / Instances / Live / Activity / Runtime Audit จะแสดงเฉพาะ instance ที่มีสิทธิ์
- หน้า Users จะแสดง user ภายใน scope ที่เข้าถึงได้
- หน้า Security Audit Log จะถูกกรองตาม session ของผู้ใช้ โดยดูได้เฉพาะ
  - event ของตัวเอง
  - event ของ instance ที่รับผิดชอบ
  - ถ้าเป็น `Owner System` จะเลือกดู `All accessible` ได้

ถ้าผู้ใช้ไม่มี `instance_ids` ระบบจะถือว่าเห็นทุก instance ที่มีสิทธิ์ตาม role นั้น ๆ

---

### 10.4 กฎพิเศษ Owner-Only

กฎต่อไปนี้บังคับใช้ที่ layer HTTP request (นอกเหนือจาก permission matrix) โดย endpoint จะ return `403 Forbidden` ทันทีและบันทึก `auth.forbidden` ลง audit log:

| การกระทำ | เงื่อนไข | ผลลัพธ์ถ้าละเมิด |
|---|---|---|
| สร้าง User ด้วย `role=owner` | ผู้เรียกต้องเป็น owner เท่านั้น | 403 + audit log |
| แก้ไข User ที่มี `role=owner` อยู่แล้ว | ผู้เรียกต้องเป็น owner เท่านั้น | 403 + audit log |
| เลื่อน User ใดก็ตามให้เป็น `role=owner` | ผู้เรียกต้องเป็น owner เท่านั้น | 403 + audit log |
| Disable User ใดก็ตาม | ต้องมี `user.disable` (owner เท่านั้น) | 403 + audit log |
| ลบ owner คนสุดท้าย | ห้ามทำ — จะ error ก่อนถึง DB | 400 Bad Request |

---

### 10.5 Security Events ที่ถูกบันทึกใน Audit Log

ทุก permission failure จะถูกบันทึกลง `security/auth_audit.jsonl` โดยอัตโนมัติ:

| Event | สาเหตุ |
|---|---|
| `auth.unauthorized` | เรียก API โดยไม่มี session |
| `auth.csrf_failed` | CSRF token ไม่ถูกต้อง |
| `auth.forbidden` | Role ไม่มีสิทธิ์เพียงพอ |
| `auth.login_failed` | Username/password ผิด |
| `auth.password_change_failed` | เปลี่ยน password แต่ใส่ current password ผิด |
