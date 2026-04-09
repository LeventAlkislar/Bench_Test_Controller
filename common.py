# common.py
# Bu dosya artık sadece geriye dönük uyumluluk için duruyor.
# Asıl kod bench_test/utils/paths.py'ye taşındı.
# Silerken buraya bak: bu dosyayı import eden yer kalmadığında silebilirsin.

from bench_test.utils.paths import (
    BASE_DIR,
    ASSETS_DIR,
    load_last_paths,
    save_last_paths,
    get_last,
    remember,
    open_file,
    save_file,
    open_dir,
)