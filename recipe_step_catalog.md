# Recipe Step Catalog

Kaynaklar:  
- `bench_test/recipe/models.py`  
- `bench_test/recipe/runner.py`  
- `bench_test/ui/tabs/recipe_tab.py`

## 1) RecipeStep Şeması

| Alan | Tip | Beklenen Değer |
|---|---|---|
| `port` | `int` | `0..8` (`0` = değişiklik yok, `1..8` = Valve A portu) |
| `valve_b_state` | `int` | `0` / `1` / `2` (`0`=yok, `1`=Load, `2`=Inject) |
| `dropview_action` | `str` | `none`, `start_dropview`, `start_measure`, `stop_measure`, `exit_dropview` |
| `duration_minutes` | `float` | `>= 0` (`0` süre, sadece DropView aksiyon adımlarında pratikte anlamlı) |
| `description` | `str` | Opsiyonel açıklama metni |

## 2) Desteklenen DropView Adımları

| Adım Adı (`dropview_action`) | Runner Davranışı | Beklenen Parametre/Önkoşul |
|---|---|---|
| `none` | DropView çağrısı yapılmaz | Ek parametre yok |
| `start_dropview` | `DropViewController.do_start_dropview()` çalıştırılır | `dropview_ctrl` bağlı olmalı |
| `start_measure` | `DropViewController.do_start_measure(scr_path)` çalıştırılır | Geçerli `.scr` yolu (`session_scr_path` veya step içi kaynak) |
| `stop_measure` | `DropViewController.do_stop_measure()` çalıştırılır | Çalışan ölçüm olması beklenir |
| `exit_dropview` | `DropViewController.do_exit_dropview()` çalıştırılır | DropView açık olmalı (değilse controller tarafı best-effort) |

## 3) Süre ve Yürütme Notları

- Adım çalıştırma sırası: DropView aksiyonu -> Valve A -> Valve B -> süre bekleme.
- `duration_minutes <= 0` ise adım, aksiyonlar tamamlandıktan sonra beklemeden biter.
- Progress event'leri sadece süre bekleme fazında üretilir.

## 4) Loop Yapıları

### 4.1 Recipe döngüsü

- Alan: `Recipe.loop_count` (`int`, varsayılan `1`)
- Tüm adım listesini baştan sona tekrar eder.

### 4.2 Step aralığı döngüsü

- Model: `StepLoop(start_step, end_step, loop_count)`
- `start_step` ve `end_step` 1-based indeks kabul edilir.
- Runner, belirtilen aralığı `loop_count` kadar genişletip yürütür.
- UI tarafında çakışan step loop tanımı engellenir.

