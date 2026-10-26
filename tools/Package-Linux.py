"""Package only the public Linux preview files, with LF line endings."""
from pathlib import Path
import hashlib
from zipfile import ZIP_DEFLATED, ZipFile

root = Path(__file__).resolve().parent.parent
files = (
    "linux/cooler_panel.py", "linux/test_cooler_panel.py", "linux/install.sh",
    "linux/cooler-panel.service", "linux/70-cooler-panel.rules", "linux/README.md", "LICENSE",
)
target = root / "dist/CoolerPanel-linux-preview-0.1.0.zip"
target.parent.mkdir(exist_ok=True)
with ZipFile(target, "w", ZIP_DEFLATED) as archive:
    for name in files:
        data = (root / name).read_bytes().replace(b"\r\n", b"\n")
        if name.endswith(".py"):
            compile(data, name, "exec")
        archive.writestr("CoolerPanel-linux/" + name, data)
with ZipFile(target) as archive:
    assert archive.testzip() is None
digest = hashlib.sha256(target.read_bytes()).hexdigest()
(target.parent / "SHA256SUMS-linux.txt").write_text(f"{digest}  {target.name}\n", encoding="ascii")
print(target)
