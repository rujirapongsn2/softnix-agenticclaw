# Sandbox Execution Plan (Softnix / Nanobot)

เอกสารนี้สรุปแผนการยกระดับความปลอดภัยสำหรับงาน Agent ระดับ Enterprise โดยยังคงความสามารถเดิมของ Agent (shell, run code, install library, ใช้เครื่องมือได้ยืดหยุ่น) ผ่านรูปแบบการรันแบบแยกสภาพแวดล้อม

## 1) เป้าหมาย

- ให้ Agent ทำงานได้เต็มความสามารถ
- ลดผลกระทบต่อ Host machine
- ป้องกันผลกระทบข้าม Instance (cross-instance)
- รองรับการเลือกโหมดต่อ Instance:
  - `sandbox` (Docker, แนะนำ)
  - `host` (โหมดเดิม, เพื่อ compatibility)

## 2) Runtime Modes ต่อ Instance

เพิ่มการตั้งค่าใน instance config เช่น:

```json
{
  "runtime": {
    "mode": "sandbox"
  }
}
```

ค่า `runtime.mode` ที่รองรับ:
- `sandbox`: รันงานใน Docker แบบ isolated
- `host`: รันบน host แบบเดิม

ค่า `runtime.sandbox.execution_strategy` ที่รองรับ:
- `persistent`: เปิด sandbox gateway ค้างต่อ instance (รูปแบบเดิม)
- `tool_ephemeral`: ให้ host gateway เป็น control plane และ launch ephemeral sandbox เฉพาะเมื่อ task นั้นมี tool call

การตั้งค่า `runtime.mode` ทำได้ 2 ช่องทาง:
- แก้ผ่าน instance config โดยตรง
- ตั้งค่าผ่านหน้า **Admin UI** ในหน้า Instance settings (ไม่จำเป็นต้องแก้ไฟล์เอง)

ค่า default ที่แนะนำ:
- Instance ใหม่: `sandbox`
- Instance เดิม: `host` แล้ว migrate ทีละตัว

## 3) โครงสร้าง Sandbox ที่แนะนำ

- 1 job = 1 container (ephemeral)
- mount เฉพาะ workspace ของ instance นั้น
- จบงานแล้ว stop + remove container ทันที
- ไม่แชร์ writable volume ข้าม instance

## 4) Security Baseline สำหรับ Sandbox

- รันด้วย non-root user
- `--cap-drop=ALL`
- `--security-opt no-new-privileges`
- เปิด seccomp/apparmor profile
- ห้าม `--privileged`
- ห้าม mount `/var/run/docker.sock`
- ใช้ read-only rootfs และเปิด writable เฉพาะจุดที่จำเป็น
- จำกัด CPU / Memory / PIDs / Disk / Timeout ต่อ job
- จำกัด network egress ด้วย allowlist ตาม policy

## 5) Dependency / Package Installation

- อนุญาต install library ได้ใน sandbox เท่านั้น
- ห้ามเขียน global environment บน host
- optional: ใช้ internal package mirror/cache ที่ตรวจสอบแล้ว
- บันทึกรายการ package change ลง audit log

## 6) Isolation ระหว่าง Instance

- แยก workspace, secrets, logs ต่อ instance
- policy network ต่อ instance
- ห้าม instance A เข้าถึง volume ของ instance B
- ทุก job ต้องมี instance_id เป็น security boundary หลัก

## 7) Control Plane Flow (Softnix Admin)

1. ผู้ใช้ trigger งานจาก Instance
2. Admin อ่าน `runtime.mode`
3. ถ้า `sandbox`: สร้าง ephemeral container แล้ว run task
4. ถ้า `host`: ใช้ runtime เดิม
5. สรุปผล + audit + cleanup

สำหรับ `tool_ephemeral`:
1. gateway/control plane รันบน `host`
2. ถ้า task ตอบได้โดยไม่ใช้ tools → ตอบ inline
3. ถ้า model เรียก tools → launch ephemeral sandbox 1 งานต่อ 1 container
4. อ่านผลลัพธ์กลับมา → ลบ container ทันที

## 8) การเปลี่ยนแปลง API/Config ที่ควรเพิ่ม

- Instance config:
  - `runtime.mode`
  - `runtime.sandbox.image`
  - `runtime.sandbox.execution_strategy`
  - `runtime.sandbox.cpu_limit`
  - `runtime.sandbox.memory_limit`
  - `runtime.sandbox.network_policy`
  - `runtime.sandbox.timeout_seconds`
- Admin API:
  - `PATCH /admin/instances/{id}` รองรับ runtime settings
  - validation ค่า mode และ sandbox policy

## 9) Migration Plan

Phase 1:
- เพิ่ม schema + UI สำหรับเลือก `runtime.mode`
- default instance ใหม่เป็น `sandbox`
- ยังรองรับ `host` เพื่อไม่ break ของเดิม

Phase 2:
- เปิด sandbox ให้ instance ที่เสี่ยงสูงก่อน (เช่นใช้ shell หนัก)
- เพิ่ม audit และ alert สำหรับ policy violation

Phase 3:
- บังคับ sandbox เป็นมาตรฐาน production
- จำกัด host mode เฉพาะกรณีมีการอนุมัติพิเศษ

## 10) Success Criteria

- ไม่มีการแก้ไขไฟล์นอก workspace จาก sandbox jobs
- ไม่มี cross-instance file access
- งานเดิมที่ต้องใช้ shell/install/run code ยังทำได้
- มี audit log ครบ: command, file ops, package install, exit status

## 10.1) Runtime Audit (implemented baseline)

- ทุก tool call ของ main agent และ subagent ถูกบันทึกลง `workspace/.nanobot/runtime-audit.jsonl`
- log ครอบคลุมอย่างน้อย:
  - `exec` command
  - file operations (`read_file`, `write_file`, `edit_file`, `list_dir`)
  - package install pattern ที่ตรวจจับได้จาก `exec`
  - result preview และ exit code (ถ้ามี)
- admin audit และ runtime audit แยกกัน:
  - admin audit = config / lifecycle / policy changes
  - runtime audit = command / file / execution events

## 11) หมายเหตุเชิงนโยบาย

- `host` mode ถือเป็น compatibility mode ไม่ใช่ secure default
- production ควรกำหนด policy ให้ `sandbox` เป็นค่าเริ่มต้นเสมอ
