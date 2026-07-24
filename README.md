# Subtitle Composer / Subtitled Video Pro

Desktop subtitle composition and video delivery tool for project-based editing,
batch transcription, open-font subtitle styling, and FFmpeg-based export.

## Project Layout

| Path | Purpose |
| --- | --- |
| `main.py` | PyQt6 desktop entry point |
| `room_*.py` | Main application rooms and workflows |
| `fonts/open/` | Bundled open font pack and per-font license files |
| `font_registry.json` | Font policy registry used by the app and release audit |
| `scripts/download_open_fonts.py` | Google Fonts starter-pack downloader |
| `scripts/download_adobe_open_fonts.py` | Adobe Source / Source Han open-font downloader |
| `requirements.txt` | Runtime Python dependencies |
| `requirements-build.txt` | Release build dependencies |
| `main.spec` | Optional local PyInstaller spec mirroring the GitHub release bundle |
| `.github/workflows/release.yml` | GitHub release build, checksum, and artifact attestation workflow |
| `LICENSE.LIST` | Direct dependency license audit list |
| `docs/DEPENDENCY_LICENSE_AUDIT.md` | Dependency and release-asset license audit report |
| `docs/SECURITY_ATTESTATION.md` | Release provenance and verification guide |
| `docs/RELEASE_PROCESS.md` | Public release checklist |

## Local Setup

Use Python 3.12. Windows is the primary local development path; macOS builds
are produced by GitHub Actions on native macOS runners.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run from source:

```powershell
python main.py
```

## Local Secrets

Do not commit `settings.json`. Start from the safe template:

```powershell
Copy-Item settings.example.json settings.json
```

Cloud sync credentials should be supplied locally through `settings.json` or
environment variables:

```powershell
$env:SUBTITLE_COMPOSER_SYNC_URL = "https://your-worker.workers.dev/"
$env:SUBTITLE_COMPOSER_CLOUD_SECRET = "your-local-secret"
```

If real credentials ever existed in this directory or a local git history,
rotate them before publishing.

## First Public Push

The prepared package is a sanitized release workspace. For the first public
GitHub push, initialize or copy this folder into the target repository after
credential rotation:

```powershell
git init
git add .
git status --short
git commit -m "Prepare public release"
git branch -M main
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

Confirm that `settings.json`, local workspaces, generated build folders, and
personal tokens are not staged.

## Release

Create a release by pushing a tag:

```powershell
git tag V0.1.16
git push origin V0.1.16
```

You can also run the `Release` workflow manually and enter a tag like `V0.1.16`.

The workflow builds:

- `SubtitleComposer-<tag>-windows-x64.zip`
- `SubtitleComposer-<tag>-macos-x64.zip`
- `SubtitleComposer-<tag>-macos-arm64.zip`

Each package includes the open font pack and a bundled FFmpeg/FFprobe runtime
prepared by the GitHub-hosted runner. The workflow also generates
`checksums.sha256`, uploads the files to GitHub Releases, and creates GitHub
artifact attestations with `actions/attest-build-provenance`.

The macOS `.app` bundles are ad-hoc signed in CI when PyInstaller succeeds. If
the hosted macOS runner cannot produce a standalone bundle, the workflow uploads
a `source-runner` fallback package with `run.command`. Developer ID notarization
is not included unless Apple signing and notarization secrets are added later.

## Verify Release Artifact

```powershell
gh release download V0.1.16 -R secure-artifacts/Subtitled-video-Pro
Get-FileHash .\SubtitleComposer-V0.1.16-windows-x64.zip -Algorithm SHA256
Get-Content .\checksums.sha256
gh attestation verify .\SubtitleComposer-V0.1.16-windows-x64.zip -R secure-artifacts/Subtitled-video-Pro
```

On macOS:

```bash
gh release download V0.1.16 -R secure-artifacts/Subtitled-video-Pro
shasum -a 256 SubtitleComposer-V0.1.16-macos-arm64.zip
cat checksums.sha256
gh attestation verify SubtitleComposer-V0.1.16-macos-arm64.zip -R secure-artifacts/Subtitled-video-Pro
```

Compare the SHA256 result with `checksums.sha256`.

## License And Compliance

The prepared open-source baseline is `GPL-3.0-only` because the app uses PyPI
`PyQt6` and `PyQt6-WebEngine`, whose open-source distribution path is
GPL-3.0-only. If the project owner has a Riverbank commercial license for
PyQt/PyQt-WebEngine, the project license can be revisited before release.

The bundled `fonts/open` package contains 21 open font files recorded in
`fonts/open/open_fonts_manifest.json`; keep each font directory's `OFL.txt`,
`LICENSE.txt`, or `LICENSE.md` when redistributing the app or project packages.

See:

- `LICENSE`
- `COPYING`
- `LICENSE.LIST`
- `THIRD_PARTY_NOTICES.md`
- `docs/DEPENDENCY_LICENSE_AUDIT.md`
- `docs/SECURITY_ATTESTATION.md`
- `docs/RELEASE_PROCESS.md`
