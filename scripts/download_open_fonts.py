import json
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FONTS_DIR = ROOT / "fonts" / "open"
MANIFEST_PATH = FONTS_DIR / "open_fonts_manifest.json"
GITHUB_API = "https://api.github.com/repos/google/fonts/contents/{path}?ref=main"


FONT_PACK = [
    {"family": "Noto Sans SC", "path": "ofl/notosanssc", "source": "Google Fonts Noto Sans SC"},
    {"family": "Noto Serif SC", "path": "ofl/notoserifsc", "source": "Google Fonts Noto Serif SC"},
    {"family": "Noto Sans", "path": "ofl/notosans", "source": "Google Fonts Noto Sans"},
    {"family": "Inter", "path": "ofl/inter", "source": "Google Fonts Inter"},
    {"family": "Open Sans", "path": "ofl/opensans", "source": "Google Fonts Open Sans"},
    {"family": "Montserrat", "path": "ofl/montserrat", "source": "Google Fonts Montserrat"},
    {"family": "Poppins", "path": "ofl/poppins", "source": "Google Fonts Poppins"},
    {"family": "Oswald", "path": "ofl/oswald", "source": "Google Fonts Oswald"},
    {"family": "Bebas Neue", "path": "ofl/bebasneue", "source": "Google Fonts Bebas Neue"},
    {"family": "Anton", "path": "ofl/anton", "source": "Google Fonts Anton"},
    {"family": "Lato", "path": "ofl/lato", "source": "Google Fonts Lato"},
    {"family": "Merriweather", "path": "ofl/merriweather", "source": "Google Fonts Merriweather"},
    {"family": "Playfair Display", "path": "ofl/playfairdisplay", "source": "Google Fonts Playfair Display"},
    {"family": "Cinzel", "path": "ofl/cinzel", "source": "Google Fonts Cinzel"},
    {"family": "Cormorant Garamond", "path": "ofl/cormorantgaramond", "source": "Google Fonts Cormorant Garamond"},
    {"family": "Libre Baskerville", "path": "ofl/librebaskerville", "source": "Google Fonts Libre Baskerville"},
    {"family": "Lora", "path": "ofl/lora", "source": "Google Fonts Lora"},
    {"family": "DM Sans", "path": "ofl/dmsans", "source": "Google Fonts DM Sans"},
    {"family": "Manrope", "path": "ofl/manrope", "source": "Google Fonts Manrope"},
    {"family": "Raleway", "path": "ofl/raleway", "source": "Google Fonts Raleway"},
    {"family": "Quicksand", "path": "ofl/quicksand", "source": "Google Fonts Quicksand"},
    {"family": "Nunito", "path": "ofl/nunito", "source": "Google Fonts Nunito"},
    {"family": "Rubik", "path": "ofl/rubik", "source": "Google Fonts Rubik"},
    {"family": "Josefin Sans", "path": "ofl/josefinsans", "source": "Google Fonts Josefin Sans"},
    {"family": "League Spartan", "path": "ofl/leaguespartan", "source": "Google Fonts League Spartan"},
    {"family": "Archivo", "path": "ofl/archivo", "source": "Google Fonts Archivo"},
    {"family": "Archivo Black", "path": "ofl/archivoblack", "source": "Google Fonts Archivo Black"},
    {"family": "Barlow Condensed", "path": "ofl/barlowcondensed", "source": "Google Fonts Barlow Condensed"},
    {"family": "Bodoni Moda", "path": "ofl/bodonimoda", "source": "Google Fonts Bodoni Moda"},
    {"family": "Prata", "path": "ofl/prata", "source": "Google Fonts Prata"},
    {"family": "Yeseva One", "path": "ofl/yesevaone", "source": "Google Fonts Yeseva One"},
    {"family": "Abril Fatface", "path": "ofl/abrilfatface", "source": "Google Fonts Abril Fatface"},
    {"family": "Great Vibes", "path": "ofl/greatvibes", "source": "Google Fonts Great Vibes"},
    {"family": "Dancing Script", "path": "ofl/dancingscript", "source": "Google Fonts Dancing Script"},
    {"family": "Pacifico", "path": "ofl/pacifico", "source": "Google Fonts Pacifico"},
    {"family": "Caveat", "path": "ofl/caveat", "source": "Google Fonts Caveat"},
    {"family": "Permanent Marker", "path": "ofl/permanentmarker", "source": "Google Fonts Permanent Marker"},
]


WEIGHT_NAMES = {
    "thin": "100",
    "extralight": "200",
    "light": "300",
    "regular": "400",
    "medium": "500",
    "semibold": "600",
    "bold": "700",
    "extrabold": "800",
    "black": "900",
}


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Subtitle-Composer-font-pack/1.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def download(url, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Subtitle-Composer-font-pack/1.0"})
    with urllib.request.urlopen(req, timeout=120) as response:
        target.write_bytes(response.read())


def load_manifest():
    if not MANIFEST_PATH.exists():
        return {"schema": 1, "fonts": []}
    try:
        return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"schema": 1, "fonts": []}


def save_manifest(manifest):
    manifest["schema"] = 1
    manifest["fonts"] = sorted(
        manifest.get("fonts", []),
        key=lambda item: (str(item.get("family", "")).casefold(), str(item.get("file", "")).casefold()),
    )
    MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def infer_weight(filename):
    if "[" in filename and "wght" in filename:
        return "100 900"
    lower = filename.casefold()
    for name, weight in WEIGHT_NAMES.items():
        if re.search(rf"(^|[-_]){name}([-_.]|$)", lower):
            return weight
    return "400"


def infer_style(filename):
    return "italic" if "italic" in filename.casefold() else "normal"


def select_font_files(items):
    font_items = [
        item for item in items
        if item.get("type") == "file"
        and item.get("name", "").lower().endswith(".ttf")
        and "italic" not in item.get("name", "").casefold()
    ]
    variable = [item for item in font_items if "[" in item.get("name", "") and "wght" in item.get("name", "")]
    if variable:
        return variable

    selected = []
    preferred = ("regular", "bold")
    for token in preferred:
        for item in font_items:
            if token in item.get("name", "").casefold() and item not in selected:
                selected.append(item)
                break
    return selected or font_items[:1]


def upsert_manifest_record(manifest, record):
    fonts = manifest.setdefault("fonts", [])
    key = (record["family"].casefold(), record["file"].casefold())
    for idx, old in enumerate(fonts):
        old_key = (str(old.get("family", "")).casefold(), str(old.get("file", "")).casefold())
        if old_key == key:
            merged = dict(old)
            merged.update(record)
            fonts[idx] = merged
            return
    fonts.append(record)


def main():
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest()
    downloaded = 0
    failed = []

    for family in FONT_PACK:
        print(f"Scanning {family['family']}...")
        try:
            items = fetch_json(GITHUB_API.format(path=family["path"]))
        except Exception as exc:
            failed.append(f"{family['family']}: {exc}")
            print(f"  skipped: {exc}", file=sys.stderr)
            continue

        license_item = next((item for item in items if item.get("name", "").casefold() in {"ofl.txt", "license.txt"}), None)
        license_file = ""
        if license_item:
            license_target = FONTS_DIR / family["path"].split("/")[-1] / license_item["name"]
            try:
                download(license_item["download_url"], license_target)
                license_file = license_target.relative_to(FONTS_DIR).as_posix()
            except Exception as exc:
                failed.append(f"{family['family']} license: {exc}")
                print(f"  license skipped: {exc}", file=sys.stderr)

        for item in select_font_files(items):
            target = FONTS_DIR / family["path"].split("/")[-1] / item["name"]
            print(f"  downloading {item['name']}")
            try:
                download(item["download_url"], target)
            except Exception as exc:
                failed.append(f"{family['family']} {item['name']}: {exc}")
                print(f"  skipped file: {exc}", file=sys.stderr)
                continue
            upsert_manifest_record(manifest, {
                "family": family["family"],
                "file": target.relative_to(FONTS_DIR).as_posix(),
                "source": family["source"],
                "license": "OFL-1.1",
                "license_file": license_file,
                "license_url": "https://scripts.sil.org/OFL",
                "weight": infer_weight(item["name"]),
                "style": infer_style(item["name"]),
                "notes": "Downloaded from the Google Fonts GitHub repository. Commercial use is allowed under the SIL Open Font License; keep OFL.txt with redistributed font files.",
                "downloaded_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            downloaded += 1

    save_manifest(manifest)
    print(f"Done. Downloaded {downloaded} font file(s). Manifest: {MANIFEST_PATH}")
    if failed:
        print("Skipped font items:", file=sys.stderr)
        for item in failed:
            print(f"  - {item}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Font download failed: {exc}", file=sys.stderr)
        sys.exit(1)
