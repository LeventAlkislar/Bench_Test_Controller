# bench_test/config.py
# Tüm magic number'lar ve sabitler burada toplanır.
# Eski kodda dağınık halde duran değerlerin tek merkezi.

# ── Pencere başlıkları ─────────────────────────────────────────
DROPVIEW_WINDOW_NAME = "DropView 8400M"
MULTISCRIPT_WINDOW   = "Multiscript Editor"
CONNECTING_DIALOG    = "Connecting..."
WARNING_UNSAVED      = "Some curves have not been saved"

# ── Görüntü eşleme eşikleri ────────────────────────────────────
THRESHOLD_HIGH = 0.7   # find_on_screen varsayılanı
THRESHOLD_MID  = 0.6   # run/delete butonları için
THRESHOLD_LOW  = 0.4   # bağlantı durumu tespiti için

# ── Bekleme süreleri (saniye) ──────────────────────────────────
SLEEP_AFTER_CLICK      = 0.3   # tıklama sonrası UI'ın tepkisi için
SLEEP_AFTER_FOCUS      = 0.5   # pencere öne alındıktan sonra
SLEEP_AFTER_COMMAND    = 1.0   # klavye komutu sonrası
SLEEP_AFTER_LAUNCH     = 2.0   # exe başlatıldıktan sonra pencere için

# ── Timeout'lar (saniye) ───────────────────────────────────────
TIMEOUT_WINDOW_OPEN    = 60    # pencere görünene kadar
TIMEOUT_CONNECT        = 30    # DropSens bağlantısı
TIMEOUT_STOP_MEASURE   = 60    # sarı nokta kaybolana kadar
TIMEOUT_CLOSE_WINDOW   = 10    # pencere kapanana kadar
TIMEOUT_VALVE          = 20    # valf hareketi tamamlanana kadar

# ── Poll aralıkları (saniye) ───────────────────────────────────
POLL_INTERVAL_FAST     = 0.5
POLL_INTERVAL_NORMAL   = 1.0
POLL_INTERVAL_SLOW     = 2.0

# ── DropView kurulum yolları ───────────────────────────────────
DROPVIEW_EXE_CANDIDATES = [
    r"C:\Program Files\DropView 8400M\Dropview.exe",
    r"C:\Program Files (x86)\DropView 8400M\Dropview.exe",
]

# ── pyautogui ayarları ─────────────────────────────────────────
PYAUTOGUI_FAILSAFE = False
PYAUTOGUI_PAUSE    = 0.05
AGGREGATE_INTERVAL_MS = 10_000   # Periyodik aggregate aralığı (ms)

# ── script ayarları ─────────────────────────────────────────
DEFAULT_REPEAT_COUNT      = 1440
DEFAULT_WAIT_DURATION_SEC = 47.5