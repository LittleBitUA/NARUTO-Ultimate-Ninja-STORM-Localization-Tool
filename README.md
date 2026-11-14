<div align="center">

# 🍥 NARUTO UA Toolkit

**A localization toolkit for the NARUTO: Ultimate Ninja STORM series on PC**

Translate the text, redraw the textures and extend the fonts of STORM 1, STORM 2 and STORM 3 —
from one window, without touching the original game files.

![Version](https://img.shields.io/badge/version-4.1.1-F97316?style=for-the-badge)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-22C55E?style=for-the-badge)
![Made in Ukraine](https://img.shields.io/badge/made%20in-Ukraine-FFD700?style=for-the-badge&labelColor=0057B7)

[**Download**](https://github.com/LittleBitUA/NARUTO-Ultimate-Ninja-STORM-Localization-Tool/releases/latest) · [Українською](README_UA.md) · [Changelog](CHANGELOG.md) · [Telegram](https://t.me/LittleBitUA)

</div>

---

## ✨ What it does

NARUTO UA Toolkit was built to make the **Ukrainian localization** of the STORM games, and it works for
any language that needs new letters in the game font. It unpacks the game's `XFBIN` resources into
files a translator can actually edit, and packs the edited files back.

| | |
| --- | --- |
| 📝 **Text** | Every string of a game in **one TXT file** — dialogue, menus, item names, mission text, subtitles. |
| 🖼️ **Textures** | Interface and story-title images exported to **PNG** and rebuilt after editing. |
| 🔤 **Fonts** | Missing letters are **added** to the game font from any TTF / OTF; the original glyphs stay. |
| 📊 **Progress** | A live completion percentage for each game. |
| 🛡️ **Safety** | Originals are only ever read. Every install makes backups, and STORM 3 has one-click rollback. |
| 🌐 **Two languages** | The interface is in English and Ukrainian and follows the Windows display language. |

## 🎮 Supported games

| Game | Text | Textures | Font | Result of a build |
| --- | :---: | :---: | :---: | --- |
| **NARUTO: Ultimate Ninja STORM** | ✅ | ✅ | ✅ | built **and installed** into the game, with backups |
| **NARUTO SHIPPUDEN: Ultimate Ninja STORM 2** | ✅ | ✅ | ✅ | a test build and a ZIP — nothing is installed |
| **NARUTO SHIPPUDEN: Ultimate Ninja STORM 3 Full Burst / HD** | ✅ | — | ✅ | three editions built from one TXT, installed, with rollback |

> [!NOTE]
> The toolkit contains **no game files**. You need your own copy of each game with its resources
> extracted (the `dataRegion_extracted\data` folder).

## 🚀 Quick start

1. Install **Python 3.10 or newer** on Windows.
2. Download the [latest release](https://github.com/LittleBitUA/NARUTO-Ultimate-Ninja-STORM-Localization-Tool/releases/latest) and unpack it anywhere.
3. Run **`INSTALL_DEPENDENCIES.bat`** once — it installs `customtkinter`, `Pillow` and `fonttools`.
4. Start the tool with **`START_WINDOWS.bat`**.
5. Pick a game in the sidebar, point the tool at your game and workspace folders, and press **Export TXT**.

The interface language follows Windows (Ukrainian or English); you can switch it at the bottom of the sidebar.

## 📝 Translating text

Each game gets a single file in your workspace folder — `STORM1.ALL.UA.txt`, `STORM2.ALL.UA.txt`,
`STORM3.ALL.UA.txt` — with a manifest next to it. A record looks like this:

```text
### 12 — message\003_xxx\S2.txt · рядок 4 · EN: Believe it!
Вір у це!

###
```

- Write the translation between the `### N — …` header and the closing `###`.
- Leave a record empty to keep the English original.
- Don't change headers or their order, and don't edit the manifest.
- A line that starts with `#` or `\` is written as `\#` or `\\`.

Then use the three buttons:

| Button | What happens |
| --- | --- |
| **Export TXT** | Creates the TXT from the game. Running it again carries your translations over and keeps a backup. |
| **Verify** | Checks the whole TXT against the game files. Writes nothing. |
| **Build** | Packs the TXT into `XFBIN` (and installs it, for STORM 1 and STORM 3). |

**Small things that save time**

- Speaker names in STORM 1 are translated **once** and inserted everywhere they appear.
- STORM 3 text is shared: one TXT produces separate files for Full Burst, HD WIN32 and HD WIN64.
- If the empty line before a closing `###` gets deleted by accident, the tool restores it before
  packing and keeps the previous file as `*.before_autofix.bak`.

## 🖼️ Textures

Export turns the textures of a whole folder tree into PNG files, one folder per `XFBIN`. Edit the PNG
in any editor — keep its size and alpha channel — and press **Build changed PNGs**.

- A repeated export **never overwrites** a PNG you have already edited; it only adds missing ones.
- Only changed images are rebuilt; everything else in the `XFBIN` stays byte-identical.
- Supported: `NTP3` textures in BC1 / DXT1 and BC3 / DXT5 without mip levels.

## 🔤 Fonts

The games ship without Cyrillic. The toolkit renders the missing letters from a TTF / OTF of your
choice and appends them to the game font — the original glyphs and their metrics are left alone.

| Game | How the letters are added |
| --- | --- |
| **STORM 1** | New rows are appended to the atlas. Every new cell gets a 4 px empty margin, and the packed atlas is read back to prove nothing bleeds into a neighbouring glyph. |
| **STORM 2** | Letters go into the unused rows of the 512×512 atlas. A separate action repairs fonts built by older versions. |
| **STORM 3** | 66 letters are added and the atlas grows from 512×256 to 512×512; the font is installed with backups. |

For STORM 1 the tool also shows **how the result will look in the game**: sample text is drawn from
the already packed atlas, each glyph placed by its own record. Letters that look the same in Latin and
Cyrillic can be copied from the game's own font, so they match the original style exactly.

## 🛡️ Safety

- Original game resources are **read-only** for the tool. Builds go to a separate `Build` folder.
- Before anything is replaced inside the game folder, the previous file is backed up.
- Inputs are checked against the SHA-256 recorded at export time — a build refuses to run against
  the wrong version of a file.
- Rebuilt files are parsed again and checked before they are written.
- Long operations run in a separate process, so the window stays responsive.

## ⚠️ Status

This is a working tool made for a real translation project, not a finished product for every setup.

- Rebuilt files pass structural checks, but **only the game itself is the final test** — check fonts
  and text in game after each build.
- Loading the STORM 3 text through loose files in the **HD edition** is not yet confirmed in game.
- The enlarged STORM 3 font atlas (512×512) is not yet confirmed by the engine.
- Textures with mip levels, and formats other than BC1 / BC3, are not rebuilt.
- Notes inside the generated reports and the comment lines at the top of the TXT are in Ukrainian.

## 🗂️ Project layout

| File | Role |
| --- | --- |
| `natool.py` | The interface |
| `jobs.py` | Runs each operation in its own process |
| `i18n.py` | Interface language (English / Ukrainian) |
| `txtfmt.py` | The TXT format, automatic repair, progress counting |
| `storm1_text.py` · `storm2_text.py` · `storm3_text.py` | Text: export, build, install |
| `storm1_font.py` · `storm2_font.py` · `storm3_font.py` | Fonts |
| `textures.py` | Textures: PNG ⇄ XFBIN |
| `audit.py` | Read-only reports about game resources |
| `xfbin.py` | Reader for the NUCC / XFBIN container |

## 🧪 Tests

```text
py -3 -m pip install pytest
py -3 -m pytest tests
```

Most of the suite runs on synthetic files generated by the tests themselves. Tests that need real
game resources are skipped until you point them at your own files through environment variables
(`STORM1_FONT_TEST_FILE`, `STORM1_FONT_TTF`, `STORM1_TEXTURE_TEST_DIR`, `STORM2_TEST_FILE`,
`STORM2_FONT_TEST_SOURCE`; the rest are named in the test files).

## 👤 Author

<div align="center">

**Dmytro Bidlov** · «Little Bit» Team

🇺🇦 **Made in Ukraine**

[![Telegram](https://img.shields.io/badge/Telegram-LittleBitUA-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/LittleBitUA)

</div>

## 📄 License

Released under the [MIT License](LICENSE).

---

<sub>NARUTO, NARUTO SHIPPUDEN and Ultimate Ninja STORM are trademarks of their respective owners.
This is an unofficial fan-made tool and is not affiliated with or endorsed by them. It includes no
game assets.</sub>
