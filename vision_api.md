# vision.py Public API

Kaynak: `bench_test/dropview/vision.py`  
Not: Burada `_` ile başlamayan modül-seviye public fonksiyonlar listelenir.

| Metod İmzası | Açıklama |
|---|---|
| `find_on_screen(image_path, threshold=0.7)` | Ekranda verilen template görselini arar, bulunursa merkez `(x, y)` koordinatını döner. |
| `match_score_on_screen(image_path)` | Verilen template için ekrandaki en yüksek template matching skorunu döner. |
| `wait_for_image(image_path, timeout=60, poll_interval=1.0, threshold=0.7)` | Verilen görsel ekranda görünene kadar belirtilen süre boyunca bekler. |
| `wait_for_image_gone(image_path, timeout=60, poll_interval=1.0, threshold=0.7)` | Verilen görsel ekrandan kaybolana kadar belirtilen süre boyunca bekler. |

