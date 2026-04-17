import xml.etree.ElementTree as ET
from xml.dom import minidom
import os


def generate_dropview_script(
    method_file: str,
    output_csv: str,
    repeat_times: int,
    wait_ms: int,
    output_script_path: str
):
    """
    DropView ölçüm scriptini (.scr) kullanıcı girdilerine göre üretir.

    Parametreler:
        method_file       : .tp uzantılı method dosyasının tam yolu
                            Örn: C:\\Lab\\Methods\\PAD 5 Sample.tp
        output_csv        : Sonuçların yazılacağı CSV dosyasının tam yolu
                            Örn: C:\\Lab\\Results\\test_001.csv
        repeat_times      : Kaç kez ölçüm yapılacağı (RUN-EXPORT-WAIT döngüsü)
                            Örn: 1440 → 24 saat (47sn periyotla)
        wait_ms           : Ölçümler arası bekleme süresi (milisaniye)
                            Örn: 47000 → 47 saniye
        output_script_path: Üretilen .scr dosyasının kaydedileceği yol
                            Örn: C:\\Lab\\Scripts\\session_001.scr
    """

    # Kök element
    root = ET.Element("root")

    # Dosya tipi bildirimi
    ET.SubElement(root, "file", type="normal", version="1.0")

    # Eylemler bloğu
    actions = ET.SubElement(root, "actions")

    # 1. LOADMETHOD
    load = ET.SubElement(actions, "action", type="LOADMETHOD")
    ET.SubElement(load, "file").text = method_file
    ET.SubElement(load, "nodes").text = "0"

    # 2. REPEAT — sonraki 3 komutu (RUN, EXPORTCURVES, WAIT) döngüye alır
    repeat = ET.SubElement(actions, "action", type="REPEAT")
    ET.SubElement(repeat, "orders").text = "3"
    ET.SubElement(repeat, "times").text = str(repeat_times)

    # 3. RUN
    run = ET.SubElement(actions, "action", type="RUN")
    ET.SubElement(run, "nodes").text = "0"

    # 4. EXPORTCURVES
    export = ET.SubElement(actions, "action", type="EXPORTCURVES")
    ET.SubElement(export, "file").text = output_csv
    ET.SubElement(export, "nodes").text = "0"
    ET.SubElement(export, "usedecimalpoint").text = "true"

    # 5. WAIT
    ET.SubElement(actions, "action",
                  message="",
                  timeMS=str(wait_ms),
                  type="WAIT")

    # XML'i güzel formatlı yaz
    raw_xml = ET.tostring(root, encoding="unicode")
    formatted = minidom.parseString(raw_xml).toprettyxml(indent="  ")

    # minidom'un eklediği fazladan XML deklarasyonunu temizle,
    # yerine orijinaldekiyle birebir aynı deklarasyonu koy
    lines = formatted.splitlines()
    lines[0] = '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
    final_xml = "\n".join(lines)

    # Çıktı dizini yoksa oluştur
    os.makedirs(os.path.dirname(output_script_path), exist_ok=True)

    with open(output_script_path, "w", encoding="utf-8") as f:
        f.write(final_xml)

def prompt_and_generate():
    """
    Kullanıcıdan interaktif girdi alarak DropView scripti üretir.
    """
    print("=" * 55)
    print("  DropView Ölçüm Script Üretici")
    print("=" * 55)

    method_file = input(
        "\n[1] Method dosyasının tam yolu (.tp):\n  > "
    ).strip()

    output_csv = input(
        "\n[2] Çıktı CSV dosyasının tam yolu (.csv):\n  > "
    ).strip()

    repeat_times = int(input(
        "\n[3] Tekrar sayısı (ör. 1440 = 24 saat):\n  > "
    ).strip())

    wait_sec = float(input(
        "\n[4] Ölçümler arası bekleme süresi (saniye, ör. 47):\n  > "
    ).strip())
    wait_ms = int(wait_sec * 1000)

    output_script_path = input(
        "\n[5] Üretilecek script dosyasının tam yolu (.scr):\n  > "
    ).strip()

    print()
    generate_dropview_script(
        method_file=method_file,
        output_csv=output_csv,
        repeat_times=repeat_times,
        wait_ms=wait_ms,
        output_script_path=output_script_path
    )

    # Özet
    print("\n--- Özet ---")
    print(f"  Method     : {method_file}")
    print(f"  CSV çıktı  : {output_csv}")
    print(f"  Tekrar     : {repeat_times}  (~{repeat_times * wait_sec / 3600:.1f} saat)")
    print(f"  Bekleme    : {wait_sec}sn  ({wait_ms}ms)")
    print(f"  Script     : {output_script_path}")


if __name__ == "__main__":
    prompt_and_generate()
