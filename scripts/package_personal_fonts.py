"""Package local personal fonts for manual installation.

Personal fonts are intentionally not tracked in Git. This zip is for the owner
of this workstation to keep in private storage and unpack into fonts/personal.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PERSONAL_DIR = ROOT / "fonts" / "personal"
RELEASE_DIR = ROOT / "release"
FONT_EXTS = {".ttf", ".otf", ".ttc", ".otc"}
KEEP_EXTS = FONT_EXTS | {".txt", ".md", ".license", ".copyright"}


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


README = """Subtitle Composer 个人字体包 - 找好的字体，需要自己放

重要说明:
- 这是你本机整理好的个人字体包，需要你自己放入软件目录 fonts/personal。
- 里面可能包含个人使用/非商用授权字体；不要直接上传公开仓库，不要当作软件内置字体再分发。
- 发视频前请按每个字体自己的授权说明判断；商业用途需要单独购买或确认授权。

怎么放:
1. 关闭 Subtitle Composer。
2. 解压本包。
3. 把 fonts/personal 里的字体文件夹复制到软件目录的 fonts/personal。
4. 打开软件，进设置刷新内置字体包，或重启软件。

备注:
- GitHub 代码包不会包含这些字体文件，这是为了保护授权和隐私。
- 如果字体名称在软件里看起来没变化，通常是字体内部 family name 相同，可以换另一个字体或查看字体授权文件。
"""


def should_include(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name in {".DS_Store", "Thumbs.db"}:
        return False
    return path.suffix.lower() in KEEP_EXTS or path.name.lower().startswith(("readme", "license", "ofl"))


def package_personal_fonts(output: Path | None = None) -> Path:
    if not PERSONAL_DIR.exists():
        raise FileNotFoundError(f"Personal fonts folder does not exist: {PERSONAL_DIR}")
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    output = output or RELEASE_DIR / f"SubtitleComposer-selected-personal-fonts-place-yourself-{stamp}.zip"

    included = []
    font_count = 0
    total_bytes = 0
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README_找好的字体_需要自己放.txt", README)
        for path in sorted(PERSONAL_DIR.rglob("*"), key=lambda p: str(p).casefold()):
            if not should_include(path):
                continue
            rel = path.relative_to(ROOT).as_posix()
            zf.write(path, rel)
            size = path.stat().st_size
            total_bytes += size
            if path.suffix.lower() in FONT_EXTS:
                font_count += 1
            included.append({"file": rel, "bytes": size})
        manifest = {
            "schema": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source": str(PERSONAL_DIR),
            "font_file_count": font_count,
            "total_bytes": total_bytes,
            "included": included,
            "install_to": "fonts/personal",
            "license_note": "Personal/local font package. Keep private unless every font license permits redistribution.",
        }
        zf.writestr("personal_font_package_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Package Subtitle Composer personal fonts for private/manual installation.")
    parser.add_argument("--output", type=Path, default=None, help="Optional output zip path")
    args = parser.parse_args()
    target = package_personal_fonts(args.output)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())