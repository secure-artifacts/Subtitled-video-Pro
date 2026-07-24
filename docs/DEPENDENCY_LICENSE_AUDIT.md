# Dependency License Audit Report

## Project Information

- Project: Subtitle Composer / Subtitled Video Pro
- Project type: Python desktop app with bundled open font assets
- Audit date: 2026-07-24
- Audit baseline: public GitHub open-source distribution
- Scope: direct runtime dependencies; build tools, external executables, and
  bundled font assets are tracked separately.

## Compliance Conclusion

Final assessment: conditionally compliant for open-source distribution.

Condition: distribute the project under `GPL-3.0-only`, include the complete
corresponding source, preserve third-party notices, keep bundled font license
files, and ship release provenance/checksum materials.

Reason: the direct runtime dependencies include `PyQt6` and `PyQt6-WebEngine`.
Their PyPI open-source distribution path is `GPL-3.0-only`, with a Riverbank
commercial license available as an alternative. Without documented commercial
PyQt/PyQt-WebEngine licensing, the project should not be publicly distributed
under MIT, Apache-2.0, or proprietary-only terms.

## Audit Summary

| Metric | Value |
| --- | ---: |
| Direct runtime dependencies | 4 |
| Confirmed licenses | 4 |
| Unknown licenses | 0 |
| Low risk | 2 |
| Needs attention | 2 |
| Manual confirmation required | 0 |
| Publication-relevant bundled font files | 47 |

## License Distribution

| License | Count | Notes |
| --- | ---: | --- |
| GPL-3.0-only or commercial | 2 | Decides the public distribution baseline unless commercial PyQt licensing is documented |
| Apache-2.0 | 2 | Compatible with GPL-3.0-only; preserve notices |
| MIT | 0 | Removed with the obsolete Web tools |

Bundled font assets: 47 files under `OFL-1.1` or compatible open font
licenses, recorded in `fonts/open/open_fonts_manifest.json`.

## Direct Dependency Details

| Dependency | Version | License | Source | Conclusion |
| --- | --- | --- | --- | --- |
| PyQt6 | 6.11.0 | GPL-3.0-only or Riverbank commercial license | https://pypi.org/project/PyQt6/ | Requires GPL-3.0-only distribution unless commercial licensing is documented |
| PyQt6-WebEngine | 6.11.0 | GPL-3.0-only or Riverbank commercial license | https://pypi.org/project/PyQt6-WebEngine/ | Same PyQt commercial/GPL boundary; Riverbank states PyQt-WebEngine is not LGPL |
| requests | 2.34.2 | Apache-2.0 | https://pypi.org/project/requests/ | Compatible with GPL-3.0-only |
| playwright | 1.59.0 | Apache-2.0 | https://pypi.org/project/playwright/ | Compatible with GPL-3.0-only; track bundled browser notices if packaged |

## Release-Relevant Assets

| Asset | Quantity | License / Notice | Source | Conclusion |
| --- | ---: | --- | --- | --- |
| Open font pack | 47 font files | OFL-1.1 or compatible open font licenses | `fonts/open/open_fonts_manifest.json` | May be redistributed with the GPL-3.0-only project; keep each font license file |
| FFmpeg | Release workflow bundles FFmpeg/FFprobe under `vendor/<platform>/ffmpeg` | GPL/LGPL depending on build variant; Windows Gyan essentials builds are GPLv3 | https://www.gyan.dev/ffmpeg/builds/ and https://ffmpeg.org/legal.html | Preserve upstream notices and source/source-offer information |
| PyInstaller | Build tool | GPL-2.0-or-later with bootloader exception | https://pyinstaller.org/en/stable/license.html | May be used for builds; preserve provenance |
| Playwright browsers | Browser runtime installed during release build | Chromium/browser component licenses | https://playwright.dev/python/ | If bundled into artifacts, keep browser notices under review |

## Risk Findings

### License Compatibility

No direct runtime dependency is incompatible with a `GPL-3.0-only` project
baseline.

If the project changes to MIT, Apache-2.0, or proprietary-only distribution,
`PyQt6` and `PyQt6-WebEngine` become the key licensing blockers unless a valid
Riverbank commercial license is documented.

| Option | Notes |
| --- | --- |
| Keep GPL-3.0-only | Recommended for the current public release baseline |
| Document Riverbank commercial licensing | Allows a different project license if the entitlement is valid and recorded |
| Replace the GUI framework | Possible but requires significant engineering work |

### Unknown Licenses

All direct runtime dependency licenses are confirmed.

### External Components

| Component | Risk | Recommendation |
| --- | --- | --- |
| FFmpeg release runtime | CI bundles platform FFmpeg/FFprobe binaries | Preserve upstream notices and source/source-offer information |
| Open font pack | OFL fonts can be redistributed, but modified fonts must respect reserved font name rules | Keep each font directory's license file and update the manifest after font changes |
| settings.json | Local config may contain API keys, cloud sync secrets, and local paths | Do not commit or package `settings.json`; use `settings.example.json` |

## Generated Files

| File | Purpose |
| --- | --- |
| `LICENSE.LIST` | Direct dependency license list |
| `LICENSE` | Project license compliance summary |
| `COPYING` | Short GPL-3.0-only project license notice |
| `requirements.txt` | Python runtime dependencies |
| `requirements-build.txt` | Python build dependencies |
| `THIRD_PARTY_NOTICES.md` | Third-party notices summary |

## Follow-Up Recommendations

1. Confirm the project owner accepts `GPL-3.0-only` public distribution.
2. If closed-source or permissive licensing is planned, first obtain and record
   valid Riverbank commercial PyQt/PyQt-WebEngine licensing.
3. Ship `LICENSE`, `COPYING`, `LICENSE.LIST`, `THIRD_PARTY_NOTICES.md`, this
   report, bundled font license files, release checksums, and build provenance
   with binary artifacts.
4. Re-run this audit after dependency, bundled font, or FFmpeg distribution
   changes.
