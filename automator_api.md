# automator.py Public API

Kaynak: `bench_test/dropview/automator.py`  
Not: Burada `_` ile başlamayan modül-seviye public fonksiyonlar listelenir.

| Metod İmzası | Açıklama |
|---|---|
| `find_window(title_keyword, timeout=60, poll_interval=1.0)` | Başlığında verilen metni içeren ilk görünür pencereyi bulur. |
| `window_exists(title_keyword)` | Başlığında verilen metni içeren görünür pencere olup olmadığını döner. |
| `wait_for_window_close(title_keyword, timeout=60, poll_interval=0.5)` | Verilen başlıktaki pencere kapanana kadar bekler. |
| `get_window_pid(hwnd)` | Pencere handle bilgisinden process PID değerini döner. |
| `find_owned_dialogs(owner_hwnd)` | Verilen owner pencereye ait görünür modal/owned dialogları listeler. |
| `close_owned_dialogs(owner_hwnd, log_fn=None)` | Verilen owner pencerenin dialoglarını Enter ile kapatmayı dener. |
| `ensure_dialog_watchdog(log_fn=None)` | DropView dialoglarını izleyen watchdog thread'ini başlatır/aktif tutar. |
| `stop_dialog_watchdog()` | Dialog watchdog thread'ini durdurur ve izleme durumunu temizler. |
| `wait_until_connected(timeout=30, poll_interval=1.0)` | DropView bağlı duruma gelene kadar bekler. |
| `wait_until_disconnected(timeout=10, poll_interval=1.0)` | DropView bağlantısı kesilene kadar bekler. |
| `get_dropview_connection_scores()` | Bağlı/bağlı değil görsel skorlarını ve pencere durumunu döner. |
| `step_launch_dropview()` | Sadece DropView uygulamasını başlatır, bağlantı kurmaz. |
| `step_connect_dropsens(target_com=None, log_fn=None)` | Manuel COM seçimiyle bağlantı kurar, gerekirse Ctrl+C fallback dener. |
| `step_disconnect_dropsens()` | Ctrl+D ile DropSens bağlantısını keser. |
| `step_stop_measure(log_fn=None)` | Ölçümü durdurur, bekler ve Multiscript Editor penceresini kapatır. |
| `step_exit_dropview(config, log_fn=None)` | Bağlantıyı kesip DropView uygulamasını kapatır (gerekirse force-close uygular). |
| `step_start_dropview(config, log_fn=None)` | DropView'i sağlıklı durumda başlatır ve DropSens bağlantısını kurar. |
| `step_start_measure(config, log_fn=None)` | Script yükleyip Multiscript üzerinden ölçümü başlatır. |

