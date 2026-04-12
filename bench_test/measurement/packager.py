# bench_test/measurement/packager.py
# -*- coding: utf-8 -*-
"""
Packager
========
Statik dosyaları oturum dizinine kopyalar ve gerektiğinde içeriklerini günceller.

Sorumluluklar:
- .tp dosyasını measurement_parameters/ altına kopyalamak
- .scr dosyasını measurement_parameters/ altına kopyalamak
  VE içindeki EXPORTCURVES yolunu measurements/ dizinine yönlendirmek
- recipe.json dosyasını measurement_parameters/ altına kopyalamak
- Her kopyalanan dosyayı session.register_file() ile kaydetmek
- package_root bilgisini last_paths.json'a yazmak

Kullanım:
    packager = Packager(session)
    packager.pack_tp(r"C:\...\Sil.tp")
    packager.pack_script(r"C:\...\Sil.scr")     # CSV yolu otomatik güncellenir
    packager.pack_recipe(r"C:\...\recipe.json")
    packager.finalize()                          # last_paths.json güncellenir
"""

import os
import shutil
import xml.etree.ElementTree as ET

from bench_test.measurement.session import MeasurementSession
from bench_test.utils.paths import remember


class PackagerError(Exception):
    """Paketleme sırasında oluşan hatalar."""
    pass


class Packager:
    """
    Bir MeasurementSession için statik dosyaları hazırlar.

    Parameters
    ----------
    session : MeasurementSession
        Dosyaların kopyalanacağı oturum.
    """

    def __init__(self, session: MeasurementSession):
        self.session = session

    # ── Genel yardımcı ───────────────────────────────────────────

    def _copy_to_params(self, source_path: str, dest_name: str = None) -> str:
        """
        Kaynak dosyayı measurement_parameters/ altına kopyalar.

        Parameters
        ----------
        source_path : str
            Kopyalanacak dosyanın tam yolu.
        dest_name : str, optional
            Hedef dosya adı. Verilmezse kaynak dosya adı kullanılır.

        Returns
        -------
        str
            Kopyalanan dosyanın tam yolu.
        """
        if not os.path.isfile(source_path):
            raise PackagerError(f"Dosya bulunamadı: {source_path}")

        name = dest_name or os.path.basename(source_path)
        dest = os.path.join(self.session.params_dir, name)
        shutil.copy2(source_path, dest)
        return dest

    # ── .tp dosyası ──────────────────────────────────────────────

    def pack_tp(self, tp_path: str) -> str:
        """
        .tp dosyasını measurement_parameters/ altına kopyalar.

        Returns
        -------
        str
            Kopyalanan dosyanın tam yolu.
        """
        dest_name = f"{self.session.part_number}.tp"
        dest = self._copy_to_params(tp_path, dest_name)
        self.session.register_file("tp", dest)
        self.session.save()
        return dest

    # ── .scr dosyası ─────────────────────────────────────────────

    def pack_script(self, scr_path: str) -> str:
        """
        .scr dosyasını measurement_parameters/ altına kopyalar ve
        EXPORTCURVES yolunu measurements/ dizinine yönlendirir.

        .scr içindeki CSV yolu şu şekilde güncellenir:
            {measurements_dir}\\{part_number}-{NNN}.csv
        DropView sıra numarasını kendi ekler, biz sadece temel adı veriyoruz.

        Returns
        -------
        str
            Kopyalanan (ve güncellenen) .scr dosyasının tam yolu.
        """
        dest_name = f"{self.session.part_number}.scr"
        dest = self._copy_to_params(scr_path, dest_name)

        # EXPORTCURVES yolunu güncelle
        new_csv_base = os.path.join(
            self.session.measurements_dir,
            f"{self.session.part_number}.csv"
        )
        self._patch_scr_csv(dest, new_csv_base)

        self.session.register_file("script", dest)
        self.session.save()
        return dest

    def _patch_scr_csv(self, scr_path: str, new_csv_path: str):
        """
        .scr XML dosyasındaki EXPORTCURVES action'ının file değerini günceller.

        Parameters
        ----------
        scr_path : str
            Güncellenecek .scr dosyasının tam yolu.
        new_csv_path : str
            Yeni CSV temel yolu (DropView sıra numarasını kendisi ekler).
        """
        try:
            ET.register_namespace("", "")   # namespace'leri koru
            tree = ET.parse(scr_path)
            root = tree.getroot()

            updated = False
            actions = root.find("actions")
            if actions is None:
                raise PackagerError(".scr dosyasında <actions> bulunamadı.")

            for action in actions.iter("action"):
                if action.get("type") == "EXPORTCURVES":
                    file_el = action.find("file")
                    if file_el is not None:
                        file_el.text = new_csv_path
                        updated = True
                        break

            if not updated:
                raise PackagerError(".scr dosyasında EXPORTCURVES action'ı bulunamadı.")

            # Orijinal encoding'i koruyarak yaz
            tree.write(scr_path, encoding="UTF-8", xml_declaration=True)

        except ET.ParseError as e:
            raise PackagerError(f".scr dosyası parse edilemedi: {e}")

    # ── recipe.json ──────────────────────────────────────────────

    def pack_recipe(self, recipe_path: str) -> str:
        """
        recipe.json dosyasını measurement_parameters/ altına kopyalar.

        Returns
        -------
        str
            Kopyalanan dosyanın tam yolu.
        """
        dest = self._copy_to_params(recipe_path, "recipe.json")
        self.session.register_file("recipe", dest)
        self.session.save()
        return dest

    # ── Finalize ─────────────────────────────────────────────────

    def finalize(self):
        """
        Paketleme tamamlandığında çağrılır:
        - package_root'u last_paths.json'a yazar
        - Oturum durumunu PENDING'den çıkarmaz (recipe başlatınca IN_PROGRESS olur)
        """
        # package_root = {part_number} dizininin üstü
        part_dir     = os.path.dirname(self.session.session_dir)
        package_root = os.path.dirname(part_dir)
        remember("package_root", package_root)

    # ── Toplu paketleme (kolaylık metodu) ────────────────────────

    def pack_all(
        self,
        tp_path: str,
        scr_path: str,
        recipe_path: str = None,
    ):
        """
        Tüm statik dosyaları tek çağrıyla paketler.

        Parameters
        ----------
        tp_path : str
            .tp dosyasının tam yolu.
        scr_path : str
            .scr dosyasının tam yolu.
        recipe_path : str, optional
            recipe.json dosyasının tam yolu. Verilmezse atlanır.
        """
        self.pack_tp(tp_path)
        self.pack_script(scr_path)
        if recipe_path and os.path.isfile(recipe_path):
            self.pack_recipe(recipe_path)
        self.finalize()

    # ── Script yolunu dışarıya sun ────────────────────────────────

    def get_packed_scr_path(self) -> str:
        """
        Paketlenmiş .scr dosyasının tam yolunu döndürür.
        recipe_tab.py bu yolu DropViewController'a iletecek.
        """
        return self.session.get_file_path("script") or ""
