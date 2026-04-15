# ════════════════════════════════════════════════════════════════
# main_window.py — 3 değişiklik
# ════════════════════════════════════════════════════════════════

# DEĞİŞİKLİK 1 — Import ekle (diğer tab importlarının yanına)
from bench_test.ui.tabs.package_tab import PackageTab

# DEĞİŞİKLİK 2 — __init__ içinde, recipe_tab oluşturulduktan SONRA
self.package_tab = PackageTab(self.script_tab, self.recipe_tab)

# DEĞİŞİKLİK 3 — tabs.addTab satırlarına, Script Editor ile Recipe arasına ekle
tabs.addTab(self.package_tab, "Package")
# Sonuç sırası:
#   Connection | Manual Control | Script Editor | Package | Recipe Control | Full Log

# DEĞİŞİKLİK 4 — log_signal bağlantılarına package_tab ekle
# Mevcut satır:
#   for tab in [self.conn_tab, self.manual_tab, self.script_tab, self.recipe_tab]:
# Yeni satır:
for tab in [self.conn_tab, self.manual_tab, self.script_tab,
            self.package_tab, self.recipe_tab]:
    tab.log_signal.connect(self.log_tab.append)

# DEĞİŞİKLİK 5 — recipe_tab'a package_tab referansını ver
# recipe_tab oluşturulurken package_tab henüz yok, sonradan ver:
self.recipe_tab.package_tab = self.package_tab


# ════════════════════════════════════════════════════════════════
# recipe_tab.py — 3 değişiklik
# ════════════════════════════════════════════════════════════════

# DEĞİŞİKLİK 1 — __init__ parametresine package_tab ekle
# Mevcut:
def __init__(self, ctrl_a, ctrl_b, dv_ctrl, script_tab=None):
# Yeni:
def __init__(self, ctrl_a, ctrl_b, dv_ctrl, script_tab=None, package_tab=None):
    ...
    self.package_tab = package_tab   # __init__ gövdesine ekle

# DEĞİŞİKLİK 2 — _save_recipe() metodunda, json.dump satırından SONRA
# recipe kaydedilince path'i package_tab'a bildir:
if self.package_tab:
    self.package_tab._refresh_refs()
# (Bunun için recipe_tab'ın kaydedilen yolu tutması gerekiyor — aşağıya bak)

# DEĞİŞİKLİK 3 — _current_recipe_path takibi
# __init__ gövdesine ekle:
self._current_recipe_path = ""

# _save_recipe() içinde, path alındıktan sonra:
self._current_recipe_path = path

# _load_recipe() içinde, path alındıktan sonra:
self._current_recipe_path = path

# DEĞİŞİKLİK 4 — _start_recipe() başına paketi oluştur
# Mevcut ilk satır:
#   if not self.ctrl_a.is_connected() and not self.ctrl_b.is_connected():
# Bunun ÖNÜNE ekle:
if self.package_tab and not self.package_tab.build_package():
    return

# DEĞİŞİKLİK 5 — _poll_queue() içinde "completed" ve "finished" bloklarına ekle
# Her ikisine de:
if self.package_tab:
    self.package_tab.on_recipe_completed()

# DEĞİŞİKLİK 6 — _stop_recipe() içine ekle
if self.package_tab:
    self.package_tab.on_recipe_aborted()
