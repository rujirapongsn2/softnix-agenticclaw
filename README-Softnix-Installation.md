# Softnix Installation Guide for Ubuntu Sandbox Mode

คู่มือนี้อธิบายการติดตั้งโปรเจกต์นี้บน **Ubuntu** เพื่อใช้งาน **Softnix Admin + sandbox mode** โดยให้รัน `nanobot` จาก source บน host และใช้ **Docker Engine** เป็น sandbox runtime

## แนวทางที่ใช้ในคู่มือนี้

โหมดที่แนะนำสำหรับเครื่อง Ubuntu ของคุณคือ:

- host ติดตั้ง source code ของโปรเจกต์นี้
- ใช้ `uv` สร้าง `.venv` และติดตั้ง `nanobot`
- ใช้ `Docker Engine` สำหรับ sandbox runtime ของแต่ละ instance
- ใช้ `Softnix Admin` เป็น control plane กลาง

เหมาะกับกรณีที่คุณ:

- ต้องการแก้โค้ดใน repo นี้ได้ทันที
- ต้องการใช้หลาย instance
- ต้องการเปิดใช้งาน sandbox mode จริง
- ต้องการมี admin UI สำหรับจัดการ instance

## สรุป requirement

ต้องมี:

- Ubuntu
- Git
- Python `>= 3.11`
- `uv`
- Docker Engine

อาจต้องมีเพิ่มตามการใช้งาน:

- Node.js `>= 20` และ `npm` ถ้าจะใช้ WhatsApp bridge
- Playwright ถ้าจะใช้ browser automation

## Step 1: เตรียม Ubuntu

แนะนำให้ใช้:

- Ubuntu `24.04 LTS`

ถ้าเป็น Ubuntu รุ่นเก่ากว่าและ Python ยังไม่ถึง `3.11` ให้ติดตั้ง Python เวอร์ชันใหม่ก่อน

อัปเดต package list และลง package พื้นฐาน:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates gnupg lsb-release software-properties-common
```

## Step 2: ติดตั้ง Python 3.12

### กรณี Ubuntu 24.04 ขึ้นไป

```bash
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
```

### กรณี Ubuntu 22.04 และยังไม่มี Python 3.11+

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev
```

ตรวจสอบเวอร์ชัน:

```bash
python3.12 --version
```

## Step 3: ติดตั้ง uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

จากนั้นโหลด PATH ของ shell ปัจจุบัน:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

ตรวจสอบว่า `uv` ใช้งานได้:

```bash
uv --version
```

ถ้าต้องการให้ใช้งานได้ทุกครั้งหลังเปิด shell ใหม่ ให้เพิ่มบรรทัดนี้ใน `~/.bashrc` หรือ `~/.zshrc`

```bash
export PATH="$HOME/.local/bin:$PATH"
```

## Step 4: ติดตั้ง Docker Engine บน Ubuntu

ลบ package เก่าถ้ามี:

```bash
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do sudo apt-get remove -y "$pkg"; done
```

เพิ่ม Docker official repository:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
```

ติดตั้ง Docker Engine:

```bash
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

เปิด service และตั้งให้ start อัตโนมัติ:

```bash
sudo systemctl enable --now docker
```

เพิ่ม user ปัจจุบันเข้า group `docker`:

```bash
sudo usermod -aG docker "$USER"
```

จากนั้น **logout/login ใหม่ 1 ครั้ง** หรือใช้คำสั่งนี้ใน shell ปัจจุบัน:

```bash
newgrp docker
```

ตรวจสอบว่า Docker ใช้งานได้:

```bash
docker --version
docker info
```

## Step 5: Clone โปรเจกต์

```bash
git clone https://github.com/HKUDS/nanobot.git
cd nanobot
```

ถ้าคุณใช้ fork หรือ private remote ของตัวเอง ให้ใช้ URL ของ repo ที่คุณใช้งานจริงแทน

## Step 6: ตรวจ host ก่อนติดตั้ง

ใน repo นี้มีสคริปต์ตรวจเครื่องให้แล้ว

ตรวจแบบ Ubuntu + sandbox mode:

```bash
bash scripts/check_linux_host.sh --ubuntu --sandbox
```

ถ้าคุณจะใช้ WhatsApp bridge ด้วย ให้ตรวจเพิ่ม Node.js:

```bash
bash scripts/check_linux_host.sh --ubuntu --sandbox --require-node
```

ถ้าคุณจะใช้ browser automation ด้วย และต้องการเช็ก Playwright ด้วย:

```bash
bash scripts/check_linux_host.sh --ubuntu --sandbox --require-playwright
```

## Step 7: ติดตั้งโปรเจกต์จาก source

### แบบแนะนำสำหรับ Ubuntu sandbox mode

```bash
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12
```

สคริปต์นี้จะ:

- รัน preflight check
- สร้าง `.venv`
- ติดตั้งโปรเจกต์ด้วย `uv`
- สร้างโฟลเดอร์ `~/.nanobot`, `~/.softnix/admin`, `~/.softnix/instances`

### ถ้าจะใช้ WhatsApp bridge ด้วย

```bash
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12 --with-whatsapp
```

### ถ้าจะใช้ Playwright ด้วย

```bash
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12 --with-playwright
```

### ถ้าต้องให้ Playwright ลง browser dependencies ของระบบด้วย

```bash
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12 --with-playwright-deps
```

## Step 8: ตรวจว่า nanobot พร้อมใช้งาน

```bash
.venv/bin/nanobot status
```

ถ้าต้องการ initialize พื้นฐานของ `~/.nanobot`:

```bash
.venv/bin/nanobot onboard
```

## Step 9: สร้าง Softnix instance แรก

ตัวอย่าง:

```bash
.venv/bin/nanobot softnix-init \
  --instance-id default-prod \
  --name "Default Production" \
  --owner default \
  --env prod \
  --repo-root "$(pwd)" \
  --nanobot-bin "$(pwd)/.venv/bin/nanobot"
```

คำสั่งนี้จะสร้าง:

- `~/.softnix/admin/instances.json`
- `~/.softnix/instances/default-prod/`
- `config.json`
- `workspace/`
- `logs/`
- `run/`
- `scripts/`

## Step 10: ปรับ config ของ instance ให้เป็น sandbox mode

เปิดไฟล์:

```bash
nano ~/.softnix/instances/default-prod/config.json
```

ตรวจให้แน่ใจว่ามีค่า runtime สำหรับ sandbox ตามนโยบายของคุณ

ตัวอย่างแนวคิดที่ควรตั้ง:

```json
{
  "runtime": {
    "mode": "sandbox",
    "sandbox": {
      "execution_strategy": "persistent"
    }
  }
}
```

ถ้าต้องการกำหนด image, cpu, memory, network policy หรือ timeout ให้เติมใน `runtime.sandbox` ตามนโยบายที่คุณต้องการ

## Step 11: เปิด Softnix Admin

```bash
.venv/bin/nanobot softnix-admin --registry ~/.softnix/admin/instances.json
```

จากนั้นเปิดเบราว์เซอร์:

```text
http://127.0.0.1:18880
```

## Step 12: Start instance แรก

```bash
~/.softnix/instances/default-prod/scripts/start.sh
```

ตรวจสถานะ:

```bash
~/.softnix/instances/default-prod/scripts/status.sh
```

ถ้าต้องการ restart:

```bash
~/.softnix/instances/default-prod/scripts/restart.sh
```

## Step 13: ตรวจ sandbox mode ทำงานจริง

สิ่งที่ควรตรวจ:

- `docker ps` ต้องเห็น container ของ instance ตอน instance รันอยู่
- `status.sh` ต้องคืนค่า `running`
- หน้า Softnix Admin ต้องเห็น instance และจัดการ start/stop ได้
- log ของ instance ต้องไม่มี error เรื่อง Docker

คำสั่งที่ใช้ดูคร่าว ๆ:

```bash
docker ps
cat ~/.softnix/instances/default-prod/logs/gateway.err.log
cat ~/.softnix/instances/default-prod/logs/gateway.log
```

## ถ้าจะใช้ WhatsApp bridge

ติดตั้ง Node.js `>= 20` ก่อน

ตัวอย่างติดตั้งจาก NodeSource:

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs
```

ตรวจสอบ:

```bash
node --version
npm --version
```

จากนั้นรัน setup ใหม่พร้อม `--with-whatsapp`

```bash
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12 --with-whatsapp
```

## ถ้าจะใช้ Playwright

รัน:

```bash
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12 --with-playwright
```

หรือถ้าต้องการให้ Playwright ลง browser dependencies ให้ด้วย:

```bash
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12 --with-playwright-deps
```

## ปัญหาที่พบบ่อย

### 1. `Docker daemon is not reachable`

ให้ตรวจ:

```bash
sudo systemctl status docker
```

ถ้ายังไม่ขึ้น ให้ลอง:

```bash
sudo systemctl enable --now docker
```

### 2. `Current user is not in the docker group`

ให้รัน:

```bash
sudo usermod -aG docker "$USER"
newgrp docker
```

หรือ logout/login ใหม่

### 3. `Python version is too old`

ใช้ `python3.12` ตามคู่มือนี้ และรัน setup ด้วย:

```bash
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12
```

### 4. `uv is required but was not found on PATH`

ให้โหลด PATH ใหม่:

```bash
export PATH="$HOME/.local/bin:$PATH"
uv --version
```

### 5. instance start ไม่ขึ้นใน sandbox mode

ตรวจไฟล์:

```bash
cat ~/.softnix/instances/default-prod/logs/gateway.err.log
cat ~/.softnix/instances/default-prod/logs/gateway.log
```

และตรวจว่า:

- Docker ใช้งานได้จริง
- config ของ instance ตั้ง `runtime.mode` เป็น `sandbox`
- user ปัจจุบันเรียก Docker ได้โดยไม่ติด permission

## คำสั่งหลักที่ใช้บ่อย

```bash
bash scripts/check_linux_host.sh --ubuntu --sandbox
bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12
.venv/bin/nanobot status
.venv/bin/nanobot onboard
.venv/bin/nanobot softnix-admin --registry ~/.softnix/admin/instances.json
~/.softnix/instances/default-prod/scripts/start.sh
~/.softnix/instances/default-prod/scripts/status.sh
```

## สรุป flow ที่แนะนำ

1. ติดตั้ง Python, uv, Docker Engine บน Ubuntu
2. Clone repo
3. รัน `bash scripts/check_linux_host.sh --ubuntu --sandbox`
4. รัน `bash scripts/setup_linux_host.sh --ubuntu --sandbox --python python3.12`
5. รัน `nanobot softnix-init`
6. ปรับ `config.json` ของ instance ให้เป็น sandbox mode
7. เปิด `softnix-admin`
8. start instance และตรวจ `docker ps`
