# 📱 Softnix Mobile Channel Test Guide

ไฟล์นี้รวบรวมคำสั่ง `curl` สำหรับทดสอบการเชื่อมต่อระหว่าง Mobile App (จำลอง) กับ Softnix AgenticClaw

---

## ⚙️ ขั้นตอนเตรียมการ
1.  **Restart Admin:** รัน `./softnixclaw admin restart`
2.  **Enable Channel:** เข้าไปที่หน้า Admin UI > Channels > เลือก Instance ของคุณ > หา **Softnix Mobile** > กด **Enabled** > กด **Save**
3.  **Restart Instance:** ไปที่หน้า Instances > กด **Restart** ที่ Instance นั้นๆ (เพื่อให้บอทเริ่มโหลด Mobile Channel)

---

## 🧪 ขั้นตอนการทดสอบ

### 1. ลงทะเบียน Device ID
เป็นการแจ้งระบบว่า Device นี้มีสิทธิ์คุยกับบอท (จะไปเพิ่ม Device ID ใน Allowlist อัตโนมัติ)
> **หมายเหตุ:** เปลี่ยน `your-instance-id` เป็น ID จริงของคุณ (เช่น `default-bot`)

```bash
curl -X POST http://127.0.0.1:18880/admin/mobile/register \
  -H "Content-Type: application/json" \
  -d '{
    "instance_id": "your-instance-id",
    "device_id": "mobile-tester-01"
  }'
```

---

### 2. ส่งข้อความเข้าหา Agent
จำลองการพิมพ์ข้อความจากหน้าแอปมือถือ

```bash
curl -X POST http://127.0.0.1:18880/admin/mobile/message \
  -H "Content-Type: application/json" \
  -d '{
    "instance_id": "your-instance-id",
    "sender_id": "mobile-tester-01",
    "text": "สวัสดีบอท นี่คือการทดสอบจาก Mobile App"
  }'
```

---

### 3. ตรวจสอบคำตอบจาก Agent (Polling)
รอประมาณ 3-5 วินาทีเพื่อให้ AI ประมวลผล แล้วรันคำสั่งนี้เพื่อดึงคำตอบ

```bash
curl "http://127.0.0.1:18880/admin/mobile/poll?instance_id=your-instance-id&sender_id=mobile-tester-01"
```

---

## 🛠️ การแก้ไขปัญหา (Troubleshooting)
*   **คำตอบว่างเปล่า (`[]`):** ตรวจสอบว่า Instance ของคุณกำลังรันอยู่หรือไม่ในเมนู **Instances**
*   **Error 404:** ตรวจสอบว่า `your-instance-id` พิมพ์ถูกต้องตามที่ปรากฏในระบบ
*   **Error Connection Refused:** มั่นใจว่าได้รัน `./softnixclaw admin start` แล้ว
*   **ตรวจสอบไฟล์ Relay:** คุณสามารถดูการทำงานเบื้องหลังได้ที่ path:
    `~/.softnix/instances/[id]/workspace/mobile_relay/`
    - `inbound.jsonl`: ข้อความที่ส่งเข้าไป (จะถูกลบเมื่อบอทอ่านไปแล้ว)
    - `outbound.jsonl`: ข้อความที่บอทตอบกลับมา
