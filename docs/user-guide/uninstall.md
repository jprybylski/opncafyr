# Uninstalling

Uninstalling removes opncafyr for your user account: the app, its own R and tools, its settings, logs and download cache, and its icons. It never removes a study folder. Your data, runs, reports and decks stay where they are.

## Before you uninstall

1. Select **Quit** in the app. The uninstaller will not run while the app is open.
2. Support bundles are kept in the app's `logs` folder, which is removed. If you want to keep one, download it from **Settings** first, or copy it out of the `logs/support` folder.
3. Make sure no study folder is inside the app folder. The uninstaller refuses to run if one is.

## Uninstall on macOS

In Terminal, run:

```sh
"$HOME/Library/Application Support/opncafyr/installer/install.sh" uninstall
```

This removes `~/Library/Application Support/opncafyr`, the app's R in `~/Library/opncafyr`, and the **opncafyr** icon in `~/Applications`.

## Uninstall on Windows

In Command Prompt, run:

```bat
"%LOCALAPPDATA%\opncafyr\installer\install.bat" uninstall
```

This removes `%LOCALAPPDATA%\opncafyr` and the Desktop and Start Menu shortcuts. The installer's own folder goes a few seconds after the command finishes.

If you still have the installer zip, you can also run `./install.sh uninstall` (macOS) or `install.bat uninstall` (Windows) from it.

## Study folders

Study folders are never removed. Each one keeps its `.opncafyr` folder with the app's records, so a later install can open it again. To delete a study, delete its folder yourself.
