# Release Process

## Pre-Release Checklist

1. Confirm the project license baseline is acceptable: `GPL-3.0-only`.
2. Confirm no `settings.json`, API keys, Cloudflare tokens, API tokens,
   or personal local workspace paths are staged.
3. Rotate any credentials that previously existed in the directory, repository,
   release artifact, or local git history.
4. Run the dependency license audit after dependency or bundled-font changes.
5. Confirm `fonts/open/open_fonts_manifest.json` matches the bundled font files
   and that font license files remain beside the fonts.
6. Push a clean tag such as `V0.1.12`.

## Recommended First Public Push

The prepared package is intended as a sanitized public-release workspace.

For the first public GitHub repository, use one of these paths:

| Path | When To Use |
| --- | --- |
| Fresh public repo from sanitized files | Recommended first release path |
| Copy sanitized files into an existing private repo | Use only after reviewing old history and rotating credentials |
| History rewrite before public push | Use only if you must preserve existing commit history |

Example first push:

```powershell
git init
git add .
git status --short
git commit -m "Prepare public release"
git branch -M main
git remote add origin https://github.com/<owner>/<repo>.git
git push -u origin main
```

## Tag Release

```powershell
git status --short
git tag V0.1.12
git push origin V0.1.12
```

The `Release` workflow will:

1. install Python dependencies;
2. validate that required release notices and font manifest files exist;
3. reject a checkout that contains local-only `settings.json`;
4. build Windows x64 with PyInstaller on `windows-latest`;
5. build macOS x64 on `macos-15-intel`;
6. build macOS arm64 on `macos-15`;
7. bundle `fonts/open`, FFmpeg/FFprobe runtime files, `font_registry.json`, `nlp_dictionary.txt`, and notices;
8. apply ad-hoc signing to the macOS `.app` bundles;
9. create per-platform checksum files and a combined `checksums.sha256`;
10. create GitHub artifact attestations with `actions/attest-build-provenance`;
11. publish the GitHub release with GitHub-generated notes.

The macOS `.app` packages are ad-hoc signed when PyInstaller succeeds. If the
hosted macOS runner cannot produce a standalone `.app`, the workflow uploads a
`source-runner` fallback package with `run.command`. For a Gatekeeper-friendly
public macOS release, add Apple signing certificate and notarization secrets.

## Manual Workflow Release

In GitHub Actions, run `Release` manually and enter a version like `V0.1.12`.

## Verify After Release

```powershell
gh release download V0.1.12 -R secure-artifacts/Subtitled-video-Pro
Get-FileHash .\SubtitleComposer-V0.1.12-windows-x64.zip -Algorithm SHA256
Get-Content .\checksums.sha256
gh attestation verify .\SubtitleComposer-V0.1.12-windows-x64.zip -R secure-artifacts/Subtitled-video-Pro
```

For macOS:

```bash
gh release download V0.1.12 -R secure-artifacts/Subtitled-video-Pro
shasum -a 256 SubtitleComposer-V0.1.12-macos-arm64.zip
cat checksums.sha256
gh attestation verify SubtitleComposer-V0.1.12-macos-arm64.zip -R secure-artifacts/Subtitled-video-Pro
```

## Rollback

If a bad release is published, mark the GitHub release as pre-release or delete
the release artifact, then cut a new tag after fixing the issue. Avoid reusing
the same tag for public releases unless the release never left private testing.
