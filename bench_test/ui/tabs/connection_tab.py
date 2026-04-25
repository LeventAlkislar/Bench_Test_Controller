# bench_test/ui/tabs/connection_tab.py
# DEPRECATED: ConnectionTab, ManualControlTab ile birleştirildi.
# Bu shim, geçiş tamamlanana kadar eski import'ların kırılmaması için tutulmaktadır.
# Temizleme: manual_tab migrasyonu onaylandıktan sonra bu dosya kaldırılacak.

from bench_test.ui.tabs.manual_tab import ManualControlTab as ConnectionTab  # noqa: F401
