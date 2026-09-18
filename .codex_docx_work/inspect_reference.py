from pathlib import Path
from zipfile import ZipFile
import re

path = Path(r"C:\Users\LEVENT ALKISLAR\OneDrive\Desktop\Bilkent Üniversitesi UNAM.docx")

with ZipFile(path) as z:
    styles = z.read("word/styles.xml").decode("utf-8", errors="replace")
    theme = z.read("word/theme/theme1.xml").decode("utf-8", errors="replace")
    doc = z.read("word/document.xml").decode("utf-8", errors="replace")

for label, data, patterns in [
    ("styles", styles, ["<w:docDefaults", 'w:styleId="Normal"', 'w:styleId="Heading1"', 'w:styleId="Heading2"', 'w:styleId="Heading3"']),
    ("document", doc, ["<w:body"]),
]:
    print(f"\n--- {label} ---")
    for pat in patterns:
        idx = data.find(pat)
        if idx >= 0:
            print(f"\n[{pat}]")
            print(data[idx:idx + 2200])

print("\n--- theme fonts ---")
for tag in ["majorFont", "minorFont"]:
    match = re.search(rf"<a:{tag}>.*?</a:{tag}>", theme, re.S)
    print(match.group(0)[:1600] if match else f"no {tag}")
