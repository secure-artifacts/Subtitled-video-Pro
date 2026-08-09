"""Package portable user preset JSON files without secrets.

This creates a small zip that can be placed beside the app and extracted into
UserData/ so style/font/title/layout/transcription presets travel with the
software without bundling API keys from settings.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USERDATA_DIR = ROOT / "UserData"
RELEASE_DIR = ROOT / "release"

PRESET_FILES = [
    "style_presets.json",
    "signature_presets.json",
    "layout_presets.json",
    "title_caption_presets.json",
    "caption_mode_presets.json",
    "effects.json",
]


def _configure_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


README = """Subtitle Composer 字体/样式预设包

用途:
- 这是便携预设包，只包含样式、字体效果、标题、排版、听译模式等预设 JSON。
- 不包含 settings.json，避免把 API Key、云同步地址等私人配置带出去。

怎么放:
1. 关闭软件。
2. 把本包里的 UserData 文件夹解压到软件 main.py / exe 所在目录旁边。
3. 重新打开软件，预设会跟随软件目录读取。

如果你已经有自己的 UserData，请先备份同名 JSON，再决定是否覆盖。
"""


def package_user_presets(output: Path | None = None) -> Path:
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    output = output or RELEASE_DIR / f"SubtitleComposer-font-style-presets-portable-{stamp}.zip"

    included = []
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README_字体样式预设包.txt", README)
        for name in PRESET_FILES:
            path = USERDATA_DIR / name
            if not path.exists() or not path.is_file():
                continue
            zf.write(path, f"UserData/{name}")
            included.append({"file": f"UserData/{name}", "bytes": path.stat().st_size})
        manifest = {
            "schema": 1,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "source": str(USERDATA_DIR),
            "included": included,
            "excluded_private_files": ["settings.json", "Cache/", "State/", "diagnostics/"],
            "note": "Copy/extract UserData next to the app to carry font/style presets with the software.",
        }
        zf.writestr("preset_package_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    return output


def main() -> int:
    _configure_stdout()
    parser = argparse.ArgumentParser(description="Package portable Subtitle Composer user presets.")
    parser.add_argument("--output", type=Path, default=None, help="Optional output zip path")
    args = parser.parse_args()
    target = package_user_presets(args.output)
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())