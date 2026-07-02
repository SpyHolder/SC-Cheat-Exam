import threading
import time

import google.generativeai as genai
import keyboard
import requests
from PIL import ImageGrab

# ==========================================
# BAGIAN 1: KONFIGURASI API DAN TOKEN
# ==========================================
GEMINI_API_KEY = f"[YOUR GEMINI API KEY]"
TELEGRAM_BOT_TOKEN = "[YOUR BOT TOKEN API]"
TELEGRAM_CHAT_ID = "[YOUR TELEGRAM CHAT ID]"

# Konfigurasi model AI Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-3.1-flash-lite")

# ==========================================
# BAGIAN 2: FUNGSI-FUNGSI UTAMA
# ==========================================
COOLDOWN_DETIK = 20  # Jeda minimum antar request (detik)
MAKS_ANTRIAN = 3  # Maksimal request yang bisa antri

waktu_request_terakhir = 0  # Timestamp request terakhir berhasil diproses
sedang_memproses = False  # Flag apakah sedang ada request yang diproses
antrian_lock = threading.Lock()
antrian_request = 0  # Jumlah request yang sedang menunggu


def cek_dan_set_cooldown():
    """
    Mengecek apakah cooldown sudah selesai.
    Mengembalikan (boleh_lanjut: bool, sisa_waktu: float).
    """
    global waktu_request_terakhir
    sekarang = time.time()
    sisa = COOLDOWN_DETIK - (sekarang - waktu_request_terakhir)
    if sisa > 0:
        return False, sisa
    waktu_request_terakhir = sekarang
    return True, 0


# ==========================================
# BAGIAN 3: FUNGSI-FUNGSI UTAMA
# ==========================================
def kirim_ke_telegram(pesan):
    """Mengirimkan teks ke bot Telegram dengan sistem perbaikan otomatis jika format error."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    batas_karakter = 4000
    potongan_pesan = [
        pesan[i : i + batas_karakter] for i in range(0, len(pesan), batas_karakter)
    ]

    for indeks, potongan in enumerate(potongan_pesan):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": potongan,
            "parse_mode": "Markdown",
        }

        try:
            response = requests.post(url, json=payload)

            # Jika gagal karena format Markdown error (Error 400)
            if response.status_code == 400 and "parse entities" in response.text:
                print(
                    f"[WARNING] Format teks berantakan pada bagian {indeks + 1}. Mengirim ulang sebagai teks biasa..."
                )
                payload.pop("parse_mode", None)
                response = requests.post(url, json=payload)

            if response.status_code == 200:
                print(
                    f"[BERHASIL] Bagian {indeks + 1}/{len(potongan_pesan)} dikirim ke Telegram!"
                )
            else:
                print(f"[GAGAL] Telegram Error: {response.text}")

        except Exception as e:
            print(f"[ERROR] Masalah koneksi Telegram: {e}")

        time.sleep(1)


def proses_screenshot_dan_ai():
    """
    Fungsi utama: mengambil screenshot, menganalisis dengan AI,
    dan mengirim hasilnya ke Telegram.
    Sudah dilengkapi retry otomatis jika terkena rate limit (429).
    """
    global sedang_memproses, antrian_request

    MAKS_RETRY = 3
    DELAY_RETRY_AWAL = 20  # Detik jeda pertama saat kena 429

    for percobaan in range(1, MAKS_RETRY + 1):
        try:
            print(
                f"\n[PROSES] Mengambil screenshot... (Percobaan {percobaan}/{MAKS_RETRY})"
            )
            gambar_layar = ImageGrab.grab()

            print("[PROSES] Menganalisis gambar dengan AI...")
            instruksi = (
                "Tolong analisis gambar ini. Jika ini adalah pertanyaan atau soal, "
                "Berikan langsung jawaban nya, dengan penjelasan yang sangat sangat singkat"
            )

            response = model.generate_content([instruksi, gambar_layar])
            jawaban_ai = response.text

            print("[PROSES] AI selesai memproses. Mengirim ke Telegram...")
            kirim_ke_telegram(jawaban_ai)
            break  # Sukses, keluar dari loop retry

        except Exception as e:
            error_msg = str(e)

            # Tangani rate limit (Error 429)
            if "429" in error_msg or "quota" in error_msg.lower():
                if percobaan < MAKS_RETRY:
                    delay = DELAY_RETRY_AWAL * percobaan  # Makin lama tiap percobaan
                    pesan_tunggu = (
                        f"⏳ *AI sedang sibuk (percobaan {percobaan}/{MAKS_RETRY}).*\n"
                        f"Otomatis coba lagi dalam {delay} detik..."
                    )
                    print(f"\n[RATE LIMIT] {pesan_tunggu}")
                    kirim_ke_telegram(pesan_tunggu)
                    time.sleep(delay)
                else:
                    pesan_gagal = (
                        "❌ *Gagal setelah 3 percobaan.*\n"
                        "API Gemini masih terlalu sibuk. Tunggu 1 menit sebelum mencoba lagi."
                    )
                    print(f"\n[GAGAL TOTAL] {pesan_gagal}")
                    kirim_ke_telegram(pesan_gagal)
            else:
                pesan_error = f"❌ Terjadi kesalahan sistem: {e}"
                print(f"[ERROR] {pesan_error}")
                kirim_ke_telegram(pesan_error)
                break  # Error bukan rate limit, tidak perlu retry

    # Setelah selesai, reset flag pemrosesan
    with antrian_lock:
        sedang_memproses = False
        antrian_request = max(0, antrian_request - 1)
        print(f"[STATUS] Pemrosesan selesai. Antrian tersisa: {antrian_request}")


def picu_proses_background():
    """
    Dipanggil saat tombol pintas ditekan.
    Mengecek cooldown dan antrian sebelum menjalankan proses AI.
    """
    global sedang_memproses, antrian_request, waktu_request_terakhir

    with antrian_lock:
        # Cek cooldown
        sekarang = time.time()
        sisa_cooldown = COOLDOWN_DETIK - (sekarang - waktu_request_terakhir)

        if sisa_cooldown > 0:
            pesan = (
                f"⏳ *Cooldown aktif!*\n"
                f"Tunggu *{sisa_cooldown:.0f} detik* lagi sebelum screenshot berikutnya."
            )
            print(f"\n[COOLDOWN] {pesan}")
            kirim_ke_telegram(pesan)
            return

        # Cek antrian
        if antrian_request >= MAKS_ANTRIAN:
            pesan = (
                f"🚦 *Terlalu banyak request!*\n"
                f"Maks {MAKS_ANTRIAN} request sekaligus. Tunggu sebentar."
            )
            print(f"\n[ANTRIAN PENUH] {pesan}")
            kirim_ke_telegram(pesan)
            return

        # Lolos semua pengecekan — jadwalkan proses
        waktu_request_terakhir = sekarang
        antrian_request += 1
        sedang_memproses = True
        print(f"[INFO] Request diterima. Antrian: {antrian_request}/{MAKS_ANTRIAN}")

    thread = threading.Thread(target=proses_screenshot_dan_ai, daemon=True)
    thread.start()


# ==========================================
# BAGIAN 4: MENJALANKAN APLIKASI
# ==========================================
if __name__ == "__main__":
    kombinasi_tombol = "ctrl"
    keyboard.add_hotkey(kombinasi_tombol, picu_proses_background)

    print("=" * 52)
    print("  Aplikasi Asisten Layar AI (Versi Anti-Limit) Aktif!")
    print("=" * 52)
    print(f"  Tombol   : {kombinasi_tombol}")
    print(f"  Cooldown : {COOLDOWN_DETIK} detik antar screenshot")
    print(f"  Antrian  : maks {MAKS_ANTRIAN} request sekaligus")
    print(f"  Retry    : otomatis 3x jika kena rate limit")
    print("  Tekan ESC untuk mematikan aplikasi.")
    print("=" * 52)

    keyboard.wait("esc")
    print("\nAplikasi dimatikan.")
