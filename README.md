# 🤖 Bot Telegram — OLT ZTE C300

Bot Telegram untuk manajemen OLT ZTE C300 via Telnet.
Kompatibel dengan **Python 3.13+** di **macOS M1/M2** menggunakan `telnetlib3` (async).

---

## 📁 Struktur File

```
olt-zte-bot/
├── bot.py           # Bot utama (handler Telegram)
├── zte_telnet.py    # Modul koneksi Telnet ke OLT ZTE
├── config.json      # Konfigurasi (token, OLT, default)
├── requirements.txt # Dependencies Python
└── README.md        # Panduan ini
```

---

## 🛠 Persiapan Awal

### 1. Install Python 3.13+
Pastikan Python 3.13 atau lebih baru sudah terinstal:
```bash
python3 --version
```

### 2. Buat Virtual Environment
```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Edit Konfigurasi
Buka file `config.json` dan sesuaikan:

```json
{
    "bot_token": "TOKEN_DARI_BOTFATHER",
    "allowed_users": [ID_TELEGRAM_ANDA],
    "olt": {
        "ip": "192.168.90.90",
        "port": 23,
        "username": "akbar",
        "password": "qmdgv"
    },
    "onu_defaults": {
        "type": "FASTLINK",
        "tcont_profile": "10M",
        "downstream_limit": "30M",
        "vlan": 100,
        "service_name": "HSI"
    }
}
```

**Cara mendapatkan Token Bot:**
1. Buka Telegram → cari `@BotFather`
2. Ketik `/newbot` → ikuti instruksi
3. Salin token API yang diberikan

**Cara mendapatkan ID Telegram:**
1. Cari bot `@userinfobot` di Telegram
2. Ketik `/start` → catat angka ID Anda

---

## 🚀 Menjalankan Bot

```bash
# Pastikan venv aktif
source venv/bin/activate

# Jalankan
python bot.py
```

Output: `🤖 Bot ZTE C300 aktif | OLT: 192.168.90.90`

---

## 📋 Daftar Perintah Bot

### 🔍 Monitoring

| Perintah | Fungsi | Contoh Input |
|---|---|---|
| `/uncfg` | Tampilkan semua ONU yang belum dikonfigurasi | *(tidak perlu input)* |
| `/pppoe_status` | Status PPPoE pada ONU | `gpon-onu_1/3/1:63` |
| `/status_olt` | Status power/link port OLT atau ONU | `gpon-olt_1/3/1` atau `gpon-onu_1/3/1:63` |
| `/status_onu` | Status detail ONU (power + PPPoE) | `gpon-onu_1/3/1:63` |

### ⚙️ Konfigurasi

| Perintah | Fungsi | Format Input |
|---|---|---|
| `/daftar_onu` | Daftarkan ONU baru ke port | `gpon-olt_1/3/1 5 FASTLINK ZTEG068E0C0A` |
| `/konfigurasi_onu` | Konfigurasi tcont + gemport + service-port | `gpon-onu_1/3/1:5\|10M\|30M\|100` |
| `/pon_mng` | Konfigurasi PPPoE + security (pon-onu-mng) | `gpon-onu_1/3/1:5\|user123\|pass123\|100` |

### 📄 Running Config

| Perintah | Fungsi | Contoh Input |
|---|---|---|
| `/running_olt` | Running config interface gpon-olt | `gpon-olt_1/3/1` |
| `/running_onu` | Running config gpon-onu + pon-onu-mng | `gpon-onu_1/3/1:63` |

---

## 🧩 Contoh Alur Aktivasi ONU Baru

### 1. Cek ONU unconfigured
```
/uncfg
```
Bot akan menampilkan daftar ONU yang terdeteksi tapi belum dikonfigurasi, beserta SN-nya.

### 2. Daftarkan ONU ke port
```
/daftar_onu
→ Input: gpon-olt_1/3/1 5 FASTLINK ZTEG068E0C0A
```
Perintah yang dikirim ke OLT:
```
interface gpon-olt_1/3/1
onu 5 type FASTLINK sn ZTEG068E0C0A
```

### 3. Konfigurasi interface gpon-onu
```
/konfigurasi_onu
→ Input: gpon-onu_1/3/1:5|10M|30M|100
```
Perintah ke OLT:
```
interface gpon-onu_1/3/1:5
  tcont 1 profile 10M
  gemport 1 name HSI tcont 1
  gemport 1 traffic-limit downstream 30M
  service-port 1 vport 1 user-vlan 100 vlan 100
```

### 4. Konfigurasi PPPoE (pon-onu-mng)
```
/pon_mng
→ Input: gpon-onu_1/3/1:5|b22a38be1234567890123456|b22a38be1234567890123456|100
```
Perintah ke OLT:
```
pon-onu-mng gpon-onu_1/3/1:5
  service HSI gemport 1 iphost 1 vlan 100
  pppoe 1 nat enable user b22a38be1234567890123456 password b22a38be1234567890123456
  security-mgmt 1 state enable mode forward protocol web
write
```

### 5. Cek status PPPoE
```
/pppoe_status
→ Input: gpon-onu_1/3/1:5
```

---

## 🔧 Troubleshooting

**Bot tidak bisa connect ke OLT:**
- Pastikan Mac dan OLT berada di jaringan yang sama
- Cek IP OLT: `ping 192.168.90.90`
- Pastikan port 23 (Telnet) terbuka di OLT

**Login gagal:**
- Verifikasi username/password di `config.json`
- Coba login manual: `telnet 192.168.90.90`

**Timeout saat eksekusi:**
- Naikkan nilai `wait` di `zte_telnet.py` jika OLT lambat merespons
- Default: 2 detik per perintah, 3 detik untuk perintah `show`

**Error `telnetlib3` tidak ditemukan:**
```bash
pip install telnetlib3
```

---

## 📝 Catatan Penting

- Setiap konfigurasi otomatis diakhiri dengan perintah `write` untuk menyimpan
- Bot menggunakan threading sehingga beberapa user bisa menggunakan secara bersamaan
- Output OLT yang panjang otomatis dipotong (batas 4000 karakter per pesan Telegram)
- Semua perintah hanya bisa diakses oleh user yang terdaftar di `allowed_users`
