"""
zte_telnet.py
=============
Modul koneksi Telnet ke OLT ZTE C300 menggunakan telnetlib3
Kompatibel dengan Python 3.13+ di macOS M1

Mode CLI ZTE C300:
  HOSTNAME>           → User EXEC (read-only)
  HOSTNAME#           → Privileged EXEC (setelah 'enable')
  HOSTNAME(config)#   → Configure (setelah 'configure terminal')
  HOSTNAME(config-if)# → Interface sub-mode

Penanganan --More-- (pagination):
  ZTE C300 menampilkan "--More--" saat output panjang.
  Bot mengirim karakter SPASI untuk lanjut ke halaman berikutnya
  hingga tidak ada lagi "--More--" atau sudah mencapai prompt.

  Selain itu, sebelum semua perintah show dikirim, bot juga
  mengirim "terminal length 0" agar ZTE menonaktifkan paging
  sepenuhnya untuk sesi tersebut.
"""

import asyncio
import telnetlib3
import json
import os
import re

# ─────────────────────────────────────────────
# Load konfigurasi
# ─────────────────────────────────────────────
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
with open(CONFIG_FILE, 'r') as f:
    config = json.load(f)

OLT_IP   = config['olt']['ip']
OLT_PORT = config['olt'].get('port', 23)
OLT_USER = config['olt']['username']
OLT_PASS = config['olt']['password']

MODE_SHOW   = 'show'
MODE_CONFIG = 'config'

# Pola --More-- dari ZTE C300 (beberapa varian)
MORE_PATTERN = re.compile(r'--\s*[Mm]ore\s*--', re.IGNORECASE)


# ─────────────────────────────────────────────
# Helper: jalankan coroutine dari kode sinkron
# ─────────────────────────────────────────────
def run_async(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result(timeout=120)
        else:
            return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ─────────────────────────────────────────────
# Deteksi jenis prompt ZTE C300
# ─────────────────────────────────────────────
def _detect_prompt(buf: str) -> str:
    """
    Kembalikan jenis prompt terakhir di buffer:
      'user'       → HOSTNAME>
      'privileged' → HOSTNAME#
      'config'     → HOSTNAME(config...)#
      'more'       → ada --More-- (pagination aktif)
      'login'      → username/password prompt
      'unknown'    → tidak terdeteksi
    """
    lines = [l.rstrip() for l in buf.splitlines() if l.strip()]
    if not lines:
        return 'unknown'
    last = lines[-1]

    # Pagination --More--
    if MORE_PATTERN.search(last):
        return 'more'
    # Config sub-mode
    if re.search(r'\(config[^)]*\)#\s*$', last):
        return 'config'
    # Privileged exec
    if re.search(r'[^()>#]\s*#\s*$', last):
        return 'privileged'
    # User exec
    if re.search(r'>\s*$', last):
        return 'user'
    # Login prompt
    if any(kw in last.lower() for kw in ['username:', 'login:', 'password:']):
        return 'login'
    return 'unknown'


# ─────────────────────────────────────────────
# Fungsi async inti
# ─────────────────────────────────────────────
async def _session_async(commands: list[str], mode: str = MODE_SHOW, wait: float = 2.0) -> str:
    """
    Satu sesi Telnet lengkap dengan penanganan --More-- (pagination).

    Alur:
      1. Konek Telnet
      2. Login (username + password)
      3. enable → HOSTNAME#
      4. terminal length 0  → nonaktifkan paging untuk sesi ini
      5. Jika mode==config: configure terminal → HOSTNAME(config)#
      6. Eksekusi perintah, tangani --More-- dengan SPASI
      7. exit/end → keluar config mode
      8. write (jika config)
      9. quit
    """
    try:
        reader, writer = await asyncio.wait_for(
            telnetlib3.open_connection(OLT_IP, OLT_PORT, encoding='utf-8', connect_minwait=0.05),
            timeout=12
        )
    except asyncio.TimeoutError:
        raise ConnectionError(f"Timeout konek ke {OLT_IP}:{OLT_PORT}")
    except OSError as e:
        raise ConnectionError(f"Gagal konek ke {OLT_IP}:{OLT_PORT} → {e}")

    log = []

    # ── Baca sampai prompt atau --More-- ──
    async def read_until_prompt(timeout=8.0) -> str:
        buf = ""
        deadline = asyncio.get_event_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(reader.read(8192), timeout=min(remaining, 1.5))
            except asyncio.TimeoutError:
                p = _detect_prompt(buf)
                if p in ('user', 'privileged', 'config', 'more'):
                    break
                continue
            if not chunk:
                break
            buf += chunk
            p = _detect_prompt(buf)
            if p in ('user', 'privileged', 'config', 'more'):
                break
        return buf

    # ── Kirim perintah + baca seluruh output (tangani --More-- berulang) ──
    async def send_and_collect(cmd: str, delay: float = wait) -> str:
        """
        Kirim perintah, tangani semua halaman --More-- dengan SPASI,
        kembalikan output lengkap tanpa teks --More--.
        """
        writer.write(cmd + '\r\n')
        await asyncio.sleep(delay)

        full_output = ""
        page_timeout = delay + 5  # timeout per halaman

        while True:
            chunk = await read_until_prompt(timeout=page_timeout)
            full_output += chunk
            prompt_type = _detect_prompt(chunk)

            if prompt_type == 'more':
                # Kirim SPASI untuk lanjut ke halaman berikutnya
                writer.write(' ')
                await asyncio.sleep(0.5)
                # timeout halaman berikutnya lebih singkat (data sudah ada di OLT)
                page_timeout = 5.0
                continue
            else:
                # Sudah di prompt akhir (#, >, atau (config)#)
                break

        return full_output

    # ── Kirim tanpa mengumpulkan output besar (untuk perintah login/enable) ──
    async def send(cmd: str, delay: float = wait) -> str:
        writer.write(cmd + '\r\n')
        await asyncio.sleep(delay)
        out = await read_until_prompt(timeout=delay + 3)
        log.append(out)
        return out

    try:
        # ══ 1. Login ══
        banner = await read_until_prompt(timeout=10)
        log.append(banner)

        writer.write(OLT_USER + '\r\n')
        await asyncio.sleep(1.0)
        after_user = await read_until_prompt(timeout=6)
        log.append(after_user)

        writer.write(OLT_PASS + '\r\n')
        await asyncio.sleep(1.5)
        after_pass = await read_until_prompt(timeout=8)
        log.append(after_pass)

        combined = after_user + after_pass
        if any(kw in combined.lower() for kw in ['fail', 'invalid', 'incorrect', 'bad password']):
            raise PermissionError("Login gagal — periksa username/password di config.json")

        current_prompt = _detect_prompt(after_pass)

        # ══ 2. Enable → privileged # ══
        if current_prompt == 'user':
            out = await send('enable', delay=1.5)
            current_prompt = _detect_prompt(out)

        if current_prompt not in ('privileged', 'config'):
            raise RuntimeError(
                f"Gagal masuk privileged mode. Prompt: '{current_prompt}'\n"
                f"Output terakhir:\n{after_pass[-300:]}"
            )

        # ══ 3. Nonaktifkan paging (terminal length 0) ══
        # Ini adalah cara paling andal untuk menghilangkan --More-- di ZTE C300.
        # Dikirim sebelum apapun agar berlaku untuk seluruh sesi.
        await send('terminal length 0', delay=1.0)

        # ══ 4. configure terminal (jika mode config) ══
        if mode == MODE_CONFIG:
            out = await send('configure terminal', delay=1.5)
            current_prompt = _detect_prompt(out)
            if current_prompt not in ('config',):
                raise RuntimeError(f"Gagal masuk configure terminal. Prompt: '{current_prompt}'")

        # ══ 5. Eksekusi perintah utama ══
        cmd_outputs = []
        for cmd in commands:
            cmd = cmd.strip()
            if not cmd:
                continue
            delay = wait
            if cmd.startswith('interface ') or cmd.startswith('pon-onu-mng '):
                delay = wait + 0.5
            # Gunakan send_and_collect untuk menangani --More-- jika terminal length 0 tidak berhasil
            out = await send_and_collect(cmd, delay=delay)
            log.append(out)
            cmd_outputs.append(out)

        # ══ 6. Keluar config mode + simpan ══
        if mode == MODE_CONFIG:
            for _ in range(4):
                cp = _detect_prompt(log[-1] if log else '')
                if cp == 'privileged':
                    break
                writer.write('exit\r\n')
                await asyncio.sleep(0.8)
                out = await read_until_prompt(timeout=4)
                log.append(out)

            # Simpan ke NVRAM
            out = await send('write', delay=3.0)
            log.append(out)

        # ══ 7. Logout ══
        writer.write('quit\r\n')
        await asyncio.sleep(0.5)

    finally:
        try:
            writer.close()
        except Exception:
            pass

    return '\n'.join(cmd_outputs) if cmd_outputs else '\n'.join(log)


# ─────────────────────────────────────────────
# Antarmuka publik
# ─────────────────────────────────────────────
def send_show_commands(commands: list[str], wait: float = 3.0) -> str:
    """Eksekusi perintah SHOW (privileged exec, tanpa configure terminal)."""
    return run_async(_session_async(commands, mode=MODE_SHOW, wait=wait))


def send_config_commands(commands: list[str], wait: float = 2.0) -> str:
    """
    Eksekusi perintah KONFIGURASI.
    Otomatis: configure terminal → eksekusi → exit → write.
    """
    return run_async(_session_async(commands, mode=MODE_CONFIG, wait=wait))


def send_commands(commands: list[str], wait: float = 2.0, config_mode: bool = False) -> str:
    """Alias generik untuk backward compatibility."""
    if config_mode:
        return send_config_commands(commands, wait=wait)
    return send_show_commands(commands, wait=wait)


# ─────────────────────────────────────────────
# Utilitas: bersihkan output Telnet
# ─────────────────────────────────────────────
def clean_output(raw: str) -> str:
    """
    Bersihkan output mentah Telnet:
    - Hapus ANSI/escape codes & karakter kontrol
    - Hapus teks --More-- dan karakter backspace setelahnya
    - Hapus baris banner, prompt, dan echo perintah
    - Hilangkan baris kosong berlebihan
    """
    # 1. Hapus ANSI escape codes
    ansi = re.compile(
        r'\x1b\[[0-9;]*[mGKHFJr]'
        r'|\x1b\[[\?]?[0-9;]*[hlm]'
        r'|\x08+'   # backspace
        r'|\x0d'    # carriage return
        r'|\x00'    # null byte
    )
    raw = ansi.sub('', raw)

    # 2. Hapus --More-- beserta spasi & karakter di sekitarnya
    raw = re.sub(r'\s*--\s*[Mm]ore\s*--\s*', '\n', raw)

    # 3. Pola baris yang dibuang
    skip_patterns = [
        # Prompt semua mode ZTE
        re.compile(r'^\s*\S+[>#]\s*$'),
        re.compile(r'^\s*\S+\(config[^)]*\)#\s*$'),
        # Echo perintah (prompt + spasi + perintah)
        re.compile(r'^\s*\S+[>#]\s+\S'),
        # Banner login ZTE
        re.compile(r'^(PERHATIAN|HARAP|PT |JALAN|LEMBANG|KABUPATEN|EMAIL|HP dan|Last login|Username:|Password:|authentication)', re.I),
        re.compile(r'^\*+\s*$'),
        re.compile(r'^-{5,}\s*$'),
        # Pesan interaktif
        re.compile(r'confirm to (save|logout)', re.I),
        re.compile(r'\[yes/no\]', re.I),
        re.compile(r'Building configuration', re.I),
    ]

    lines = raw.splitlines()
    result = []
    prev_blank = False
    for line in lines:
        s = line.rstrip()
        skip = any(pat.search(s) for pat in skip_patterns)
        if skip:
            continue
        if not s:
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        result.append(s)

    return '\n'.join(result).strip()


def truncate(text: str, max_len: int = 3800) -> str:
    """Potong teks agar tidak melebihi batas karakter Telegram."""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "\n\n⚠️ [Output terpotong — terlalu panjang]"
