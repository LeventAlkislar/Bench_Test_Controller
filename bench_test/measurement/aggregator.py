# bench_test/measurement/aggregator.py
# -*- coding: utf-8 -*-
"""
Aggregator
==========
CSV ölçüm dosyalarını birleştirip tek bir xlsx dosyası üretir.
VB makrosundaki CSV_Birlestir() fonksiyonunun Python karşılığı.

Davranış:
- measurements/ dizinindeki tüm *.csv dosyalarını zaman sırasına göre işler
- Her CSV'den ilk MAX_SAMPLES_PER_FILE ölçümü alır (varsayılan: 5)
- Dosya oluşturma zamanını timestamp olarak kullanır
- Zip'ten çıkarma kaynaklı saat farkı için kullanıcıdan offset alır
- Çıktı: measurements/{part_number}.xlsx  →  Time | Current (uA)

Kullanım:
    aggregator = Aggregator(session)
    aggregator.run(parent_widget)   # GUI ile — saat offset dialog gösterir
    aggregator.run_silent(offset_hours=0)  # GUI olmadan — test için
"""

import os
import tempfile
from datetime import datetime, timedelta
from typing import Optional, List, Tuple

# openpyxl — PyQt6 projelerinde zaten mevcut olması beklenir
# Yoksa: pip install openpyxl
try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
    _OPENPYXL_OK = True
except ImportError:
    _OPENPYXL_OK = False

from bench_test.measurement.session import MeasurementSession
from bench_test.measurement.data_io import (
    MeasurementDataError,
    collect_csv_files,
    get_file_time,
    parse_csv_file,
)


class AggregatorError(Exception):
    pass


# ─────────────────────────────────────────────────────────────────
#  Düşük seviye: GUI bağımsız
# ─────────────────────────────────────────────────────────────────

def _is_output_current(csv_files: List[str], output_path: str) -> bool:
    """Return True when the existing xlsx is newer than all source CSV files."""
    if not os.path.isfile(output_path):
        return False
    try:
        output_mtime = os.path.getmtime(output_path)
        newest_csv_mtime = max(os.path.getmtime(path) for path in csv_files)
    except OSError:
        return False
    return output_mtime >= newest_csv_mtime


def _write_xlsx(rows: List[Tuple[datetime, float]], output_path: str):
    """
    Birleştirilmiş veriyi xlsx dosyasına yazar.

    Sütunlar: A=Time (datetime), B=Current (uA)
    """
    if not _OPENPYXL_OK:
        raise AggregatorError(
            "openpyxl library not found.\n"
            "Install it with: pip install openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Measurements"

    # Header
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="D9E1F2")

    ws["A1"] = "Time"
    ws["B1"] = "Current (uA)"
    for cell in [ws["A1"], ws["B1"]]:
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")

    # Veri
    for i, (ts, current) in enumerate(rows, start=2):
        ws.cell(row=i, column=1, value=ts)
        ws.cell(row=i, column=2, value=current)

    # Tarih formatı
    date_fmt = "dd.mm.yyyy hh:mm:ss"
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=1, max_col=1):
        for cell in row:
            cell.number_format = date_fmt

    # Kolon genişliği
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 16

    output_dir = os.path.dirname(output_path) or "."
    output_name = os.path.basename(output_path)
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            prefix=f".{output_name}.",
            suffix=".tmp.xlsx",
            dir=output_dir,
            delete=False,
        ) as tmp:
            tmp_path = tmp.name

        wb.save(tmp_path)
        os.replace(tmp_path, output_path)
        tmp_path = ""
    except OSError as exc:
        raise AggregatorError(f"Failed to write xlsx: {output_path}\n{exc}")
    finally:
        if hasattr(wb, "close"):
            wb.close()
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def aggregate(
    measurements_dir: str,
    part_number: str,
    offset_hours: float = 0.0,
) -> str:
    """
    CSV'leri birleştirip xlsx yazar. GUI olmadan çalışır.

    Parameters
    ----------
    measurements_dir : str
        CSV dosyalarının bulunduğu dizin.
    part_number : str
        Çıktı dosyasının adı için kullanılır: {part_number}.xlsx
    offset_hours : float
        Saat offset'i. Zip'ten çıkarma farkı için. Varsayılan: 0.

    Returns
    -------
    str
        Oluşturulan xlsx dosyasının tam yolu.
    """
    offset = timedelta(hours=offset_hours)
    try:
        csv_files = collect_csv_files(measurements_dir)
    except MeasurementDataError as e:
        raise AggregatorError(str(e))

    output_path = os.path.join(measurements_dir, f"{part_number}.xlsx")
    if _is_output_current(csv_files, output_path):
        return output_path

    all_rows: List[Tuple[datetime, float]] = []
    for path in csv_files:
        try:
            rows = parse_csv_file(path, offset)
        except MeasurementDataError as e:
            raise AggregatorError(str(e))
        all_rows.extend(rows)

    if not all_rows:
        raise AggregatorError("No data could be read from any CSV file.")

    # Zaman sırasına göre sırala (normalde dosya sırası yeterli,
    # ama güvenli olmak için)
    all_rows.sort(key=lambda r: r[0])

    _write_xlsx(all_rows, output_path)
    return output_path


# ─────────────────────────────────────────────────────────────────
#  Yüksek seviye: Session ile çalışır
# ─────────────────────────────────────────────────────────────────

class Aggregator:
    """
    MeasurementSession'a bağlı CSV birleştirici.

    Parameters
    ----------
    session : MeasurementSession
        Aktif oturum. measurements_dir ve part_number buradan alınır.
    """

    def __init__(self, session: MeasurementSession):
        self.session = session

    def run_silent(self, offset_hours: float = 0.0) -> str:
        """
        GUI olmadan çalıştırır. Test ve otomasyon için.

        Returns
        -------
        str
            Oluşturulan xlsx dosyasının tam yolu.
        """
        return aggregate(
            measurements_dir=self.session.measurements_dir,
            part_number=self.session.part_number,
            offset_hours=offset_hours,
        )

    def run(self, parent=None) -> Optional[str]:
        """
        GUI ile çalıştırır. Gerekirse saat offset dialog'u gösterir.

        İlk CSV dosyasının zamanını kullanıcıya gösterir,
        doğru saati girmesini ister (VB makrosundaki InputBox mantığı).

        Parameters
        ----------
        parent : QWidget, optional
            Dialog'un parent widget'ı.

        Returns
        -------
        str or None
            Başarılıysa xlsx yolu, iptal veya hata durumunda None.
        """
        from PyQt6.QtWidgets import QMessageBox

        try:
            csv_files = collect_csv_files(self.session.measurements_dir)
        except MeasurementDataError as e:
            QMessageBox.warning(parent, "Aggregator", str(e))
            return None

        # İlk dosyanın zamanını göster, offset sor
        first_file_time = get_file_time(csv_files[0])
        offset_hours = self._ask_offset(parent, first_file_time)
        if offset_hours is None:
            return None  # Kullanıcı iptal etti

        try:
            path = aggregate(
                measurements_dir=self.session.measurements_dir,
                part_number=self.session.part_number,
                offset_hours=offset_hours,
            )
            # session.json'a xlsx yolunu kaydet
            self.session.register_file("xlsx", path)
            self.session.save()
            QMessageBox.information(
                parent, "Aggregator",
                f"Merge complete!\n{len(csv_files)} CSV files merged.\n\nSaved: {path}")
            return path

        except AggregatorError as e:
            QMessageBox.critical(parent, "Aggregator Error", str(e))
            return None

    def _ask_offset(self, parent, first_file_time: datetime) -> Optional[float]:
        """
        Kullanıcıdan saat offset'i alır.

        Mantık (VB makrosuyla birebir):
        1. Locale'e bak — virgül ayracı kullanılıyorsa +10 saat öner
        2. Hesaplanan saati kullanıcıya öneri olarak göster
        3. Kullanıcı onaylarsa veya değiştirirse o değeri kullan

        Returns
        -------
        float or None
            Offset saat farkı (pozitif veya negatif), iptal edildiyse None.
        """
        import locale
        from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, \
            QLabel, QSpinBox, QDialogButtonBox, QGroupBox, QFormLayout

        # Locale tespiti — virgül ayracı → +10 saat
        locale.setlocale(locale.LC_ALL, "")
        decimal_sep = locale.localeconv().get("decimal_point", ".")
        suggested_hour = (first_file_time.hour + 10) % 24 \
            if decimal_sep == "," else first_file_time.hour

        dlg = QDialog(parent)
        dlg.setWindowTitle("Time Correction")
        dlg.setFixedWidth(440)
        layout = QVBoxLayout(dlg)

        # Bilgi
        info = QLabel(
            "CSV files may have incorrect timestamps due to zip extraction.\n"
            "The suggested hour is corrected based on your system locale.\n"
            "Confirm or enter the correct hour for the first file."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        # Zaman bilgisi
        grp = QGroupBox("First File Timestamp")
        form = QFormLayout(grp)

        detected_lbl = QLabel(first_file_time.strftime("%d.%m.%Y  %H:%M:%S"))
        detected_lbl.setStyleSheet("color: #888;")
        form.addRow("Detected time:", detected_lbl)

        locale_lbl = QLabel(
            f"Comma  →  +10h applied" if decimal_sep == ","
            else "Period  →  no correction")
        locale_lbl.setStyleSheet(
            "color: #FF9800;" if decimal_sep == "," else "color: #4CAF50;")
        form.addRow("Decimal separator:", locale_lbl)

        suggested_lbl = QLabel(
            first_file_time.replace(hour=suggested_hour).strftime(
                "%d.%m.%Y  %H:%M:%S"))
        suggested_lbl.setStyleSheet("font-weight: bold;")
        form.addRow("Suggested time:", suggested_lbl)

        hour_row = QHBoxLayout()
        hour_spin = QSpinBox()
        hour_spin.setRange(0, 23)
        hour_spin.setValue(suggested_hour)
        hour_spin.setFixedWidth(60)
        hour_row.addWidget(hour_spin)
        hour_row.addWidget(QLabel("(date, minutes and seconds are correct)"))
        hour_row.addStretch()
        form.addRow("Correct hour:", hour_row)
        layout.addWidget(grp)

        # Butonlar
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return None

        correct_hour = hour_spin.value()
        offset_hours = correct_hour - first_file_time.hour
        return float(offset_hours)
