"""
bot.py
======
Bot Telegram untuk Manajemen OLT ZTE C300
Kompatibel Python 3.13+ / macOS M1  |  Menggunakan telnetlib3

Perintah SHOW   → send_show_commands()   (privileged # saja)
Perintah CONFIG → send_config_commands() (masuk configure terminal dulu)

Perbaikan v3:
  - Penanganan --More-- / pagination: bot kirim 'terminal length 0'
    di awal sesi SHOW agar seluruh output tampil sekaligus.
    Fallback: kirim SPASI per halaman jika masih ada --More--.
  - Tambah /hapus_onu: no onu <id> di interface gpon-olt

Daftar perintah:
  /start            - Menu utama
  /uncfg            - ONU belum dikonfigurasi
  /pppoe_status     - Status PPPoE ONU tertentu
  /daftar_onu       - Daftarkan ONU baru ke port GPON-OLT
  /hapus_onu        - Hapus ONU dari port GPON-OLT (no onu <id>)
  /konfigurasi_onu  - Konfigurasi tcont, gemport, service-port
  /pon_mng          - Konfigurasi pon-onu-mng (PPPoE + security)
  /status_olt       - Status power/link gpon-olt atau gpon-onu
  /status_onu       - Status detail + PPPoE gpon-onu
  /running_olt      - Running-config interface gpon-olt (full, tanpa --More--)
  /running_onu      - Running-config gpon-onu + pon-onu-mng
"""

import telebot
import json
import os
import re
import threading

from zte_telnet import send_show_commands, send_config_commands, clean_output, truncate

# ══════════════════════════════════════════════
# 1. Konfigurasi
# ══════════════════════════════════════════════
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
if not os.path.exists(CONFIG_FILE):
    print(f"[ERROR] File {CONFIG_FILE} tidak ditemukan!")
    exit(1)

with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

BOT_TOKEN     = config.get('bot_token', '')
ALLOWED_USERS = config.get('allowed_users', [])
ONU_DEF       = config.get('onu_defaults', {})

if not BOT_TOKEN or BOT_TOKEN == "ISI_TOKEN_BOT_TELEGRAM_ANDA_DISINI":
    print("[ERROR] Isi bot_token yang valid di config.json!")
    exit(1)


# ══════════════════════════════════════════════
# 2. Inisialisasi Bot
# ══════════════════════════════════════════════
class SafeExceptionHandler(telebot.ExceptionHandler):
    def handle(self, exception):
        print(f"[WARNING] Exception bot: {exception}")
        return True

bot = telebot.TeleBot(BOT_TOKEN, exception_handler=SafeExceptionHandler())


# ══════════════════════════════════════════════
# 3. Helper
# ══════════════════════════════════════════════
def is_allowed(message) -> bool:
    return message.from_user.id in ALLOWED_USERS

def deny(message):
    bot.reply_to(message, "⛔ Akses ditolak. ID Anda tidak terdaftar.")

def send_typing(chat_id):
    try:
        bot.send_chat_action(chat_id, 'typing')
    except Exception:
        pass

def fmt_code(text: str) -> str:
    return f"```\n{text}\n```"

def safe_send(chat_id, text: str, parse_mode="Markdown"):
    """Kirim pesan; potong otomatis per 4000 karakter jika terlalu panjang."""
    max_len = 4000
    for i in range(0, len(text), max_len):
        try:
            bot.send_message(chat_id, text[i:i+max_len], parse_mode=parse_mode)
        except Exception as e:
            try:
                bot.send_message(chat_id, f"[Gagal kirim segmen: {e}]")
            except Exception:
                pass

def parse_gpon_interface(text: str) -> dict | None:
    """
    Parse string interface ZTE:
      gpon-olt_1/3/1        → {type:'olt', shelf, slot, port, onu:None}
      gpon-onu_1/3/1:63     → {type:'onu', shelf, slot, port, onu:'63'}
    Toleransi: gpon-olt1/3/1 (tanpa underscore) juga diterima.
    """
    s = text.strip()
    s = re.sub(r'^gpon-(olt|onu)(\d)', r'gpon-\1_\2', s, flags=re.I)
    m = re.match(r'gpon-(olt|onu)_(\d+)/(\d+)/(\d+)(?::(\d+))?', s, re.IGNORECASE)
    if not m:
        return None
    return {
        'type' : m.group(1).lower(),
        'shelf': m.group(2),
        'slot' : m.group(3),
        'port' : m.group(4),
        'onu'  : m.group(5),
    }

def del_msg(chat_id, msg_id):
    try:
        bot.delete_message(chat_id, msg_id)
    except Exception:
        pass

def _bg(fn):
    """Jalankan fungsi fn di background thread."""
    threading.Thread(target=fn, daemon=True).start()


# ══════════════════════════════════════════════
# 4. /start — Menu Utama
# ══════════════════════════════════════════════
@bot.message_handler(commands=['start', 'help'])
def cmd_start(message):
    if not is_allowed(message): return deny(message)
    olt_ip = config['olt']['ip']
    # Gunakan HTML agar tidak ada konflik dengan underscore pada nama perintah.
    # Markdown Telegram memparsing underscore sebagai italic sehingga
    # /pppoe_status, /status_onu, dsb menyebabkan error "entity not closed".
    text = (
        f"🤖 <b>Bot Manajemen OLT ZTE C300</b>\n"
        f"🔌 OLT IP: <code>{olt_ip}</code>\n\n"
        "📋 <b>Daftar Perintah:</b>\n\n"
        "🔍 <b>MONITORING</b>\n"
        "• /uncfg — ONU belum dikonfigurasi\n"
        "• /pppoe_status — Status PPPoE ONU\n"
        "• /status_olt — Status gpon-olt / gpon-onu\n"
        "• /status_onu — Status detail + PPPoE ONU\n\n"
        "⚙️ <b>KONFIGURASI</b>\n"
        "• /daftar_onu — Daftarkan ONU baru ke port\n"
        "• /hapus_onu — Hapus ONU dari port\n"
        "• /konfigurasi_onu — tcont + gemport + service-port\n"
        "• /pon_mng — PPPoE &amp; security (pon-onu-mng)\n\n"
        "📄 <b>RUNNING CONFIG</b>\n"
        "• /running_olt — Config interface gpon-olt (full)\n"
        "• /running_onu — Config gpon-onu + pon-onu-mng\n"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")


# ══════════════════════════════════════════════
# 5. /uncfg — ONU Unconfigured
# ══════════════════════════════════════════════
@bot.message_handler(commands=['uncfg'])
def cmd_uncfg(message):
    if not is_allowed(message): return deny(message)
    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message, "⏳ Mengambil daftar ONU unconfigured...")

    def task():
        try:
            output = send_show_commands(['show gpon onu uncfg'], wait=4.0)
            cleaned = clean_output(output)
            if not cleaned:
                text = "✅ Tidak ada ONU yang belum dikonfigurasi saat ini."
            else:
                text = f"📋 *ONU Belum Dikonfigurasi:*\n{fmt_code(truncate(cleaned))}"
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 6. /pppoe_status — Status PPPoE pada ONU
# ══════════════════════════════════════════════
@bot.message_handler(commands=['pppoe_status'])
def cmd_pppoe_status(message):
    if not is_allowed(message): return deny(message)
    msg = bot.reply_to(
        message,
        "📡 Masukkan interface ONU:\n_Contoh:_ `gpon-onu_1/3/1:63`",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, _pppoe_status_exec)

def _pppoe_status_exec(message):
    iface = message.text.strip()
    parsed = parse_gpon_interface(iface)
    if not parsed or parsed['type'] != 'onu' or not parsed['onu']:
        bot.reply_to(message, "❌ Format salah. Contoh: `gpon-onu_1/3/1:63`", parse_mode="Markdown")
        return

    s, sl, p, o = parsed['shelf'], parsed['slot'], parsed['port'], parsed['onu']
    iface_full = f"gpon-onu_{s}/{sl}/{p}:{o}"
    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message, f"⏳ Mengambil status PPPoE `{iface_full}`...")

    def task():
        try:
            output = send_show_commands([f"show gpon remote-onu pppoe {iface_full}"], wait=4.0)
            cleaned = clean_output(output)
            text = f"📡 *Status PPPoE — `{iface_full}`*\n{fmt_code(truncate(cleaned))}"
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 7. /daftar_onu — Daftarkan ONU baru ke port GPON-OLT
#    Perintah: configure terminal → interface gpon-olt_x/x/x
#              → onu <id> type <type> sn <sn>
# ══════════════════════════════════════════════
@bot.message_handler(commands=['daftar_onu'])
def cmd_daftar_onu(message):
    if not is_allowed(message): return deny(message)
    defs = ONU_DEF
    text = (
        "📝 *Daftarkan ONU Baru*\n\n"
        "Format (pisah spasi):\n"
        "`<interface-olt> <onu-id> <tipe> <serial-number>`\n\n"
        f"Contoh:\n`gpon-olt_1/3/1 5 {defs.get('type','FASTLINK')} ZTEG068E0C0A`"
    )
    msg = bot.reply_to(message, text, parse_mode="Markdown")
    bot.register_next_step_handler(msg, _daftar_onu_exec)

def _daftar_onu_exec(message):
    parts = message.text.strip().split()
    if len(parts) != 4:
        bot.reply_to(message,
            "❌ Harus 4 parameter:\n`<interface-olt> <onu-id> <tipe> <sn>`\n\n"
            "Contoh: `gpon-olt_1/3/1 5 FASTLINK ZTEG068E0C0A`",
            parse_mode="Markdown")
        return

    iface_olt, onu_id, onu_type, sn = parts
    parsed = parse_gpon_interface(iface_olt)
    if not parsed or parsed['type'] != 'olt':
        bot.reply_to(message, "❌ Interface OLT tidak valid. Contoh: `gpon-olt_1/3/1`", parse_mode="Markdown")
        return

    s, sl, p = parsed['shelf'], parsed['slot'], parsed['port']
    iface_full = f"gpon-olt_{s}/{sl}/{p}"
    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message,
        f"⏳ Mendaftarkan ONU `{sn}` (ID:{onu_id}) ke `{iface_full}`...",
        parse_mode="Markdown")

    def task():
        try:
            cmds = [
                f"interface {iface_full}",
                f"onu {onu_id} type {onu_type} sn {sn}",
            ]
            output = send_config_commands(cmds, wait=2.0)
            cleaned = clean_output(output)
            text = (
                f"✅ *ONU Berhasil Didaftarkan!*\n\n"
                f"🔌 Interface : `{iface_full}`\n"
                f"🔢 ONU ID   : `{onu_id}`\n"
                f"📦 Tipe     : `{onu_type}`\n"
                f"🏷 SN       : `{sn}`\n\n"
                f"📄 *Output OLT:*\n{fmt_code(truncate(cleaned))}"
            )
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 8. /hapus_onu — Hapus ONU dari port GPON-OLT
#    Perintah: configure terminal → interface gpon-olt_x/x/x
#              → no onu <id>
# ══════════════════════════════════════════════
@bot.message_handler(commands=['hapus_onu'])
def cmd_hapus_onu(message):
    if not is_allowed(message): return deny(message)
    text = (
        "🗑 *Hapus ONU dari Port*\n\n"
        "Format (pisah spasi):\n"
        "`<interface-olt> <onu-id>`\n\n"
        "Contoh:\n`gpon-olt_1/3/1 5`\n\n"
        "⚠️ _Pastikan ONU yang akan dihapus sudah tidak aktif._"
    )
    msg = bot.reply_to(message, text, parse_mode="Markdown")
    bot.register_next_step_handler(msg, _hapus_onu_konfirmasi)

def _hapus_onu_konfirmasi(message):
    """Langkah 1: Parse input, minta konfirmasi sebelum hapus."""
    parts = message.text.strip().split()
    if len(parts) != 2:
        bot.reply_to(message,
            "❌ Harus 2 parameter:\n`<interface-olt> <onu-id>`\n\n"
            "Contoh: `gpon-olt_1/3/1 5`",
            parse_mode="Markdown")
        return

    iface_olt, onu_id = parts
    parsed = parse_gpon_interface(iface_olt)
    if not parsed or parsed['type'] != 'olt':
        bot.reply_to(message, "❌ Interface OLT tidak valid. Contoh: `gpon-olt_1/3/1`", parse_mode="Markdown")
        return

    if not onu_id.isdigit():
        bot.reply_to(message, "❌ ONU ID harus berupa angka. Contoh: `5`", parse_mode="Markdown")
        return

    s, sl, p = parsed['shelf'], parsed['slot'], parsed['port']
    iface_full = f"gpon-olt_{s}/{sl}/{p}"

    # Simpan data di teks konfirmasi untuk diambil di step berikutnya
    konfirm_text = (
        f"⚠️ *KONFIRMASI HAPUS ONU*\n\n"
        f"🔌 Interface : `{iface_full}`\n"
        f"🔢 ONU ID   : `{onu_id}`\n\n"
        f"Perintah yang akan dijalankan:\n"
        f"```\n"
        f"configure terminal\n"
        f"interface {iface_full}\n"
        f"no onu {onu_id}\n"
        f"write\n"
        f"```\n\n"
        f"Ketik `YA` untuk konfirmasi, atau apapun untuk batalkan."
    )
    msg = bot.reply_to(message, konfirm_text, parse_mode="Markdown")

    # Simpan parameter ke user_data via closure
    bot.register_next_step_handler(msg, _hapus_onu_exec,
                                   iface_full=iface_full, onu_id=onu_id)

def _hapus_onu_exec(message, iface_full: str, onu_id: str):
    """Langkah 2: Jalankan penghapusan setelah konfirmasi."""
    if message.text.strip().upper() != 'YA':
        bot.reply_to(message, "❌ Penghapusan dibatalkan.")
        return

    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message,
        f"⏳ Menghapus ONU `{onu_id}` dari `{iface_full}`...",
        parse_mode="Markdown")

    def task():
        try:
            cmds = [
                f"interface {iface_full}",
                f"no onu {onu_id}",
            ]
            output = send_config_commands(cmds, wait=2.0)
            cleaned = clean_output(output)

            # Cek apakah ada error di output
            if '%Error' in output or 'Invalid' in output.lower():
                text = (
                    f"⚠️ *Peringatan — Ada pesan error dari OLT*\n\n"
                    f"🔌 Interface : `{iface_full}`\n"
                    f"🔢 ONU ID   : `{onu_id}`\n\n"
                    f"📄 *Output OLT:*\n{fmt_code(truncate(cleaned))}"
                )
            else:
                text = (
                    f"✅ *ONU Berhasil Dihapus!*\n\n"
                    f"🔌 Interface : `{iface_full}`\n"
                    f"🔢 ONU ID   : `{onu_id}`\n\n"
                    f"📄 *Output OLT:*\n{fmt_code(truncate(cleaned))}"
                )
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 9. /konfigurasi_onu — tcont + gemport + service-port
# ══════════════════════════════════════════════
@bot.message_handler(commands=['konfigurasi_onu'])
def cmd_konfigurasi_onu(message):
    if not is_allowed(message): return deny(message)
    defs = ONU_DEF
    text = (
        "⚙️ *Konfigurasi Interface ONU*\n"
        "_(tcont + gemport + service-port)_\n\n"
        "Format (pisahkan `|`):\n"
        "`<interface-onu>|<tcont-profile>|<downstream>|<vlan>`\n\n"
        "Contoh:\n"
        f"`gpon-onu_1/3/1:5|{defs.get('tcont_profile','10M')}|{defs.get('downstream_limit','30M')}|{defs.get('vlan',100)}`\n\n"
        "_Nilai kosong menggunakan default dari config.json_"
    )
    msg = bot.reply_to(message, text, parse_mode="Markdown")
    bot.register_next_step_handler(msg, _konfigurasi_onu_exec)

def _konfigurasi_onu_exec(message):
    parts = [p.strip() for p in message.text.strip().split('|')]
    if not parts or not parts[0]:
        bot.reply_to(message, "❌ Input tidak valid.", parse_mode="Markdown")
        return

    iface_onu  = parts[0]
    tcont_prof = parts[1] if len(parts) > 1 and parts[1] else ONU_DEF.get('tcont_profile', '10M')
    downstream = parts[2] if len(parts) > 2 and parts[2] else ONU_DEF.get('downstream_limit', '30M')
    vlan       = parts[3] if len(parts) > 3 and parts[3] else str(ONU_DEF.get('vlan', 100))
    svc_name   = ONU_DEF.get('service_name', 'HSI')

    parsed = parse_gpon_interface(iface_onu)
    if not parsed or parsed['type'] != 'onu' or not parsed['onu']:
        bot.reply_to(message, "❌ Format ONU salah. Contoh: `gpon-onu_1/3/1:5`", parse_mode="Markdown")
        return

    s, sl, p, o = parsed['shelf'], parsed['slot'], parsed['port'], parsed['onu']
    iface_full = f"gpon-onu_{s}/{sl}/{p}:{o}"
    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message, f"⏳ Mengonfigurasi `{iface_full}`...")

    def task():
        try:
            cmds = [
                f"interface {iface_full}",
                f"tcont 1 profile {tcont_prof}",
                f"gemport 1 name {svc_name} tcont 1",
                f"gemport 1 traffic-limit downstream {downstream}",
                f"service-port 1 vport 1 user-vlan {vlan} vlan {vlan}",
            ]
            output = send_config_commands(cmds, wait=2.0)
            cleaned = clean_output(output)
            text = (
                f"✅ *Konfigurasi ONU Berhasil!*\n\n"
                f"🔌 Interface  : `{iface_full}`\n"
                f"📶 TCONT      : `1` — Profile `{tcont_prof}`\n"
                f"💎 GemPort    : `1` — `{svc_name}`\n"
                f"⬇️ Downstream : `{downstream}`\n"
                f"🌐 VLAN       : `{vlan}`\n\n"
                f"📄 *Output OLT:*\n{fmt_code(truncate(cleaned))}"
            )
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 10. /pon_mng — Konfigurasi pon-onu-mng
# ══════════════════════════════════════════════
@bot.message_handler(commands=['pon_mng'])
def cmd_pon_mng(message):
    if not is_allowed(message): return deny(message)
    defs = ONU_DEF
    text = (
        "🛠 *Konfigurasi PON-ONU-MNG*\n"
        "_(PPPoE + Security)_\n\n"
        "Format (pisahkan `|`):\n"
        "`<interface-onu>|<pppoe-user>|<pppoe-pass>|<vlan>`\n\n"
        "Contoh:\n"
        f"`gpon-onu_1/3/1:5|b22a38be1234567890123456|b22a38be1234567890123456|{defs.get('vlan',100)}`\n\n"
        "_VLAN kosong → pakai default config.json_"
    )
    msg = bot.reply_to(message, text, parse_mode="Markdown")
    bot.register_next_step_handler(msg, _pon_mng_exec)

def _pon_mng_exec(message):
    parts = [p.strip() for p in message.text.strip().split('|')]
    if len(parts) < 3:
        bot.reply_to(message,
            "❌ Minimal 3 parameter:\n`<interface-onu>|<pppoe-user>|<pppoe-pass>`",
            parse_mode="Markdown")
        return

    iface_onu  = parts[0]
    pppoe_user = parts[1]
    pppoe_pass = parts[2]
    vlan       = parts[3] if len(parts) > 3 and parts[3] else str(ONU_DEF.get('vlan', 100))
    svc_name   = ONU_DEF.get('service_name', 'HSI')

    parsed = parse_gpon_interface(iface_onu)
    if not parsed or parsed['type'] != 'onu' or not parsed['onu']:
        bot.reply_to(message, "❌ Format ONU salah. Contoh: `gpon-onu_1/3/1:5`", parse_mode="Markdown")
        return

    s, sl, p, o = parsed['shelf'], parsed['slot'], parsed['port'], parsed['onu']
    iface_full = f"gpon-onu_{s}/{sl}/{p}:{o}"
    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message, f"⏳ Menerapkan PON-MNG pada `{iface_full}`...")

    def task():
        try:
            cmds = [
                f"pon-onu-mng {iface_full}",
                f"service {svc_name} gemport 1 iphost 1 vlan {vlan}",
                f"pppoe 1 nat enable user {pppoe_user} password {pppoe_pass}",
                f"security-mgmt 1 state enable mode forward protocol web",
            ]
            output = send_config_commands(cmds, wait=2.0)
            cleaned = clean_output(output)
            text = (
                f"✅ *PON-ONU-MNG Berhasil Dikonfigurasi!*\n\n"
                f"🔌 ONU      : `{iface_full}`\n"
                f"🌐 Service  : `{svc_name}` — VLAN `{vlan}`\n"
                f"🔑 PPPoE    : `{pppoe_user}` / `{pppoe_pass}`\n"
                f"🔐 Security : enable, forward, web\n\n"
                f"📄 *Output OLT:*\n{fmt_code(truncate(cleaned))}"
            )
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 11. /status_olt — Status gpon-olt atau gpon-onu
# ══════════════════════════════════════════════
@bot.message_handler(commands=['status_olt'])
def cmd_status_olt(message):
    if not is_allowed(message): return deny(message)
    msg = bot.reply_to(
        message,
        "📊 Masukkan interface yang ingin dicek:\n"
        "• `gpon-olt_1/3/1` — Status semua ONU di port OLT\n"
        "• `gpon-onu_1/3/1:63` — Status ONU tertentu",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, _status_olt_exec)

def _status_olt_exec(message):
    iface = message.text.strip()
    parsed = parse_gpon_interface(iface)
    if not parsed:
        bot.reply_to(message,
            "❌ Format tidak dikenali.\nContoh: `gpon-olt_1/3/1` atau `gpon-onu_1/3/1:63`",
            parse_mode="Markdown")
        return

    s, sl, p = parsed['shelf'], parsed['slot'], parsed['port']
    if parsed['type'] == 'olt':
        iface_full = f"gpon-olt_{s}/{sl}/{p}"
        cmd = f"show gpon onu state {iface_full}"
    else:
        o = parsed['onu']
        iface_full = f"gpon-onu_{s}/{sl}/{p}:{o}"
        cmd = f"show gpon onu detail-info {iface_full}"

    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message, f"⏳ Mengambil status `{iface_full}`...")

    def task():
        try:
            output = send_show_commands([cmd], wait=4.0)
            cleaned = clean_output(output)
            text = f"📊 *Status `{iface_full}`*\n{fmt_code(truncate(cleaned))}"
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 12. /status_onu — Status detail ONU + PPPoE
# ══════════════════════════════════════════════
@bot.message_handler(commands=['status_onu'])
def cmd_status_onu(message):
    if not is_allowed(message): return deny(message)
    msg = bot.reply_to(
        message,
        "📡 Masukkan ONU:\n_Contoh:_ `gpon-onu_1/3/1:63`",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, _status_onu_exec)

def _status_onu_exec(message):
    iface = message.text.strip()
    parsed = parse_gpon_interface(iface)
    if not parsed or parsed['type'] != 'onu' or not parsed['onu']:
        bot.reply_to(message, "❌ Format salah. Contoh: `gpon-onu_1/3/1:63`", parse_mode="Markdown")
        return

    s, sl, p, o = parsed['shelf'], parsed['slot'], parsed['port'], parsed['onu']
    iface_full = f"gpon-onu_{s}/{sl}/{p}:{o}"
    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message, f"⏳ Mengambil status detail `{iface_full}`...")

    def task():
        try:
            cmds = [
                f"show gpon onu detail-info {iface_full}",
                f"show gpon remote-onu pppoe {iface_full}",
            ]
            output = send_show_commands(cmds, wait=3.0)
            cleaned = clean_output(output)
            text = f"📡 *Status Lengkap `{iface_full}`*\n{fmt_code(truncate(cleaned))}"
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 13. /running_olt — Running config gpon-olt (full, tanpa --More--)
# ══════════════════════════════════════════════
@bot.message_handler(commands=['running_olt'])
def cmd_running_olt(message):
    if not is_allowed(message): return deny(message)
    msg = bot.reply_to(
        message,
        "📄 Masukkan interface OLT:\n_Contoh:_ `gpon-olt_1/3/1`",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, _running_olt_exec)

def _running_olt_exec(message):
    iface = message.text.strip()
    iface = re.sub(r'^gpon-olt(\d)', r'gpon-olt_\1', iface, flags=re.I)
    parsed = parse_gpon_interface(iface)
    if not parsed or parsed['type'] != 'olt':
        bot.reply_to(message, "❌ Format salah. Contoh: `gpon-olt_1/3/1`", parse_mode="Markdown")
        return

    s, sl, p = parsed['shelf'], parsed['slot'], parsed['port']
    iface_full = f"gpon-olt_{s}/{sl}/{p}"
    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message,
        f"⏳ Mengambil running-config `{iface_full}`...\n"
        f"_Pagination dinonaktifkan (terminal length 0)_",
        parse_mode="Markdown")

    def task():
        try:
            # terminal length 0 sudah dikirim otomatis di zte_telnet.py
            # sebelum semua perintah show, jadi output akan tampil penuh
            output = send_show_commands(
                [f"show running-config interface {iface_full}"],
                wait=5.0   # beri waktu lebih untuk output panjang
            )
            cleaned = clean_output(output)
            if not cleaned:
                text = f"⚠️ Tidak ada output untuk `{iface_full}`."
            else:
                # Kirim dalam beberapa bagian jika sangat panjang
                header = f"📄 *Running-Config `{iface_full}`*\n"
                safe_send(message.chat.id, header + fmt_code(truncate(cleaned, max_len=3500)))
                return
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 14. /running_onu — Running config gpon-onu + pon-onu-mng
# ══════════════════════════════════════════════
@bot.message_handler(commands=['running_onu'])
def cmd_running_onu(message):
    if not is_allowed(message): return deny(message)
    msg = bot.reply_to(
        message,
        "📄 Masukkan ONU:\n_Contoh:_ `gpon-onu_1/3/1:63`",
        parse_mode="Markdown"
    )
    bot.register_next_step_handler(msg, _running_onu_exec)

def _running_onu_exec(message):
    iface = message.text.strip()
    parsed = parse_gpon_interface(iface)
    if not parsed or parsed['type'] != 'onu' or not parsed['onu']:
        bot.reply_to(message, "❌ Format salah. Contoh: `gpon-onu_1/3/1:63`", parse_mode="Markdown")
        return

    s, sl, p, o = parsed['shelf'], parsed['slot'], parsed['port'], parsed['onu']
    iface_full = f"gpon-onu_{s}/{sl}/{p}:{o}"
    send_typing(message.chat.id)
    msg_wait = bot.reply_to(message,
        f"⏳ Mengambil running-config `{iface_full}` + pon-onu-mng...")

    def task():
        try:
            cmds = [
                f"show running-config interface {iface_full}",
                f"show running-config pon-onu-mng {iface_full}",
            ]
            output = send_show_commands(cmds, wait=4.0)
            cleaned = clean_output(output)
            text = f"📄 *Running-Config `{iface_full}` + PON-MNG*\n{fmt_code(truncate(cleaned))}"
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, f"❌ *Error:* `{e}`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# ══════════════════════════════════════════════
# 15. Handler perintah bebas (free CLI input)
#     Kirim perintah OLT langsung dengan awalan ! atau cli:
#     Contoh:
#       !show version
#       !show gpon onu state gpon-olt_1/3/1
#       cli:show running-config
#     Multi-baris (konfigurasi bertahap):
#       !interface gpon-olt_1/3/1
#       onu 99 type ZTE sn ZTEG12345678
# ══════════════════════════════════════════════
@bot.message_handler(func=lambda m: m.text and (
    m.text.strip().startswith('!') or
    m.text.strip().lower().startswith('cli:')
))
def cmd_free_cli(message):
    if not is_allowed(message): return deny(message)

    raw = message.text.strip()
    if raw.startswith('!'):
        cmd_input = raw[1:].strip()
    else:
        cmd_input = raw[4:].strip()

    if not cmd_input:
        bot.reply_to(message, "❌ Perintah kosong.\nContoh: <code>!show version</code>", parse_mode="HTML")
        return

    lines_cmd = [l.strip() for l in cmd_input.splitlines() if l.strip()]

    # ── Deteksi mode: SHOW jika SEMUA baris diawali "show" atau "display"
    # Selain itu → CONFIG (configure terminal).
    # Alasan: perintah show bisa dijalankan dari # maupun (config)#,
    # tapi perintah konfigurasi HANYA bisa dari (config)#.
    # Dengan default CONFIG, semua perintah apapun (hostname, ip, interface,
    # no shutdown, dsb) pasti berhasil.
    SHOW_ONLY_PREFIXES = ('show ', 'display ', 'ping ', 'traceroute ')
    is_show_only = all(
        any(line.lower().startswith(pfx) for pfx in SHOW_ONLY_PREFIXES)
        for line in lines_cmd
    )
    is_config = not is_show_only  # default CONFIG kecuali semua baris adalah show

    send_typing(message.chat.id)
    mode_label = "SHOW (privileged exec)" if is_show_only else "CONFIG (configure terminal)"
    msg_wait = bot.reply_to(message,
        "⏳ Menjalankan perintah...\n_Mode: " + mode_label + "_",
        parse_mode="Markdown")

    def task():
        try:
            if is_config:
                output = send_config_commands(lines_cmd, wait=2.0)
            else:
                output = send_show_commands(lines_cmd, wait=3.0)
            cleaned = clean_output(output)
            if not cleaned:
                cleaned = "(tidak ada output)"
            preview = cmd_input[:80].replace('`', "'")
            text = "🖥 *Output OLT:*\n" + fmt_code("$ " + preview + "\n\n" + truncate(cleaned))
            safe_send(message.chat.id, text)
        except Exception as e:
            bot.send_message(message.chat.id, "❌ *Error:* `" + str(e) + "`", parse_mode="Markdown")
        finally:
            del_msg(message.chat.id, msg_wait.message_id)

    _bg(task)


# ══════════════════════════════════════════════
# 16. Fallback untuk pesan tidak dikenal
# ══════════════════════════════════════════════
@bot.message_handler(func=lambda m: True)
def cmd_unknown(message):
    if not is_allowed(message): return deny(message)
    bot.reply_to(message,
        "❓ Perintah tidak dikenal. Ketik /start untuk bantuan.\n\n"
        "💡 <b>Tip:</b> Kirim perintah OLT langsung dengan awalan <code>!</code>\n"
        "Contoh: <code>!show version</code>",
        parse_mode="HTML")


# ══════════════════════════════════════════════
# 17. Daftarkan perintah ke menu Telegram
#     Fungsi ini membuat daftar perintah di tombol "/"
#     Telegram sesuai dengan perintah yang ada di bot.
# ══════════════════════════════════════════════
def setup_bot_commands():
    from telebot.types import BotCommand
    commands = [
        BotCommand("start",           "Tampilkan menu utama & daftar perintah"),
        BotCommand("help",            "Bantuan penggunaan bot"),
        BotCommand("uncfg",           "ONU yang belum dikonfigurasi"),
        BotCommand("pppoe_status",    "Status PPPoE pada ONU tertentu"),
        BotCommand("status_olt",      "Status port gpon-olt atau gpon-onu"),
        BotCommand("status_onu",      "Status detail + PPPoE ONU"),
        BotCommand("daftar_onu",      "Daftarkan ONU baru ke port"),
        BotCommand("hapus_onu",       "Hapus ONU dari port (no onu)"),
        BotCommand("konfigurasi_onu", "Config tcont + gemport + service-port"),
        BotCommand("pon_mng",         "Config PPPoE & security (pon-onu-mng)"),
        BotCommand("running_olt",     "Running-config interface gpon-olt"),
        BotCommand("running_onu",     "Running-config gpon-onu + pon-onu-mng"),
    ]
    try:
        bot.set_my_commands(commands)
        print("   \u2705 Daftar perintah berhasil didaftarkan ke menu Telegram")
    except Exception as e:
        print(f"   \u26a0\ufe0f  Gagal mendaftarkan perintah ke menu: {e}")


# ══════════════════════════════════════════════
# 18. Jalankan
# ══════════════════════════════════════════════
if __name__ == '__main__':
    olt_ip = config['olt']['ip']
    print(f"🤖 Bot ZTE C300 aktif")
    print(f"   OLT  : {olt_ip}")
    print(f"   User : {config['olt']['username']}")
    print(f"   Tekan Ctrl+C untuk berhenti.")
    setup_bot_commands()
    print()
    bot.infinity_polling(timeout=30, long_polling_timeout=20)
