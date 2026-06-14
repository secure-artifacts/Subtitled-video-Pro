# Third-Party Notices

Generated: 2026-06-04

This file summarizes direct runtime dependencies and release-relevant
components. See `LICENSE.LIST` for the direct dependency audit and
`docs/DEPENDENCY_LICENSE_AUDIT.md` for the full release review.

## Direct Runtime Dependencies

| Component | Version | License | Source |
| --- | --- | --- | --- |
| PyQt6 | 6.11.0 | GPL-3.0-only or Riverbank commercial license | https://pypi.org/project/PyQt6/ |
| PyQt6-WebEngine | 6.11.0 | GPL-3.0-only or Riverbank commercial license | https://pypi.org/project/PyQt6-WebEngine/ |
| requests | 2.34.2 | Apache-2.0 | https://pypi.org/project/requests/ |
| playwright | 1.59.0 | Apache-2.0 | https://pypi.org/project/playwright/ |

## Release-Relevant Components

| Component | License / Notice | Source | Notes |
| --- | --- | --- | --- |
| Open font pack | OFL-1.1 or compatible open font licenses | `fonts/open/open_fonts_manifest.json` | 22 bundled font files; keep each font directory's `OFL.txt`, `LICENSE.txt`, or `LICENSE.md` with redistributed artifacts |
| PyInstaller | GPL-2.0-or-later with bootloader exception; selected files Apache-2.0 | https://pyinstaller.org/en/stable/license.html | Used to produce Windows release artifacts |
| FFmpeg release runtime | GPL/LGPL FFmpeg components depending on build variant; Windows Gyan essentials builds are GPLv3 | https://www.gyan.dev/ffmpeg/builds/ and https://ffmpeg.org/legal.html | Release workflow bundles FFmpeg/FFprobe under `vendor/<platform>/ffmpeg` and preserves upstream notice files where provided |
| Playwright Chromium browser | Chromium and bundled component licenses | https://playwright.dev/python/ | The release build installs Chromium for Playwright; keep browser notices under review if bundled into the final artifact |

## Bundled Open Font Pack

The release workflow includes `fonts/open` in the Windows app bundle. The
current manifest records 22 font files across Google Fonts and Adobe open-source
font families, including Noto, Source Han, Source Sans, Source Serif, Inter,
Open Sans, Montserrat, Poppins, Oswald, Bebas Neue, Anton, Lato, Merriweather,
Source Code Pro, and TikTok Sans.

Font redistribution requirements:

- keep the font license files beside the font files;
- keep `fonts/open/open_fonts_manifest.json` with release records;
- do not treat OS/system fonts as redistributable bundled assets unless their
  separate license allows it;
- if a font is modified, review Reserved Font Name obligations before release.

## Project License Baseline

This repository is prepared for `GPL-3.0-only` open-source distribution because
PyPI PyQt6/PyQt6-WebEngine require GPL-compatible distribution unless a
commercial Riverbank license is documented.
