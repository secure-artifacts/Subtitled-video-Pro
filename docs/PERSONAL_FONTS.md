# Personal Fonts And Presets

Subtitle Composer keeps user presets portable by default: configuration JSON files are read from the app-side `UserData/` folder when it is writable. This lets style/font presets travel with a copied app folder instead of living only in Windows AppData.

## Presets That Travel With The App

The portable preset package should include only preset-style JSON files:

- `UserData/style_presets.json`
- `UserData/signature_presets.json`
- `UserData/layout_presets.json`
- `UserData/title_caption_presets.json`
- `UserData/caption_mode_presets.json`
- `UserData/effects.json`

Do not include `UserData/settings.json` in shareable preset packages because it may contain API keys, sync endpoints, or other private settings.

Create the preset package with:

```powershell
.venv\Scripts\python.exe scripts\package_user_presets.py
```

## Personal Font Files

Personal fonts belong in `fonts/personal/`. They are loaded for local use, but they are intentionally ignored by Git.

Create the private/manual font package with:

```powershell
.venv\Scripts\python.exe scripts\package_personal_fonts.py
```

The generated zip is labeled as selected personal fonts that must be placed manually. Keep it private unless every font license explicitly allows redistribution.

Install by extracting/copying the zip contents so the font folders end up under:

```text
fonts/personal/
```

Then restart the app or refresh fonts in Settings.