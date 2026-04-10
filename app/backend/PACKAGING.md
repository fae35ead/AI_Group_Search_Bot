# Windows EXE Packaging

Run `build_exe.bat` from `app/backend`.

What the script does:
- builds the Vite frontend into `app/frontend/dist`
- installs backend and build dependencies into `app/backend/.venv`
- installs Playwright Chromium so it can be bundled
- runs PyInstaller with `ai-group-discovery.spec`

Output:
- executable: `app/backend/dist/ai-group-discovery.exe`

Runtime behavior:
- the EXE starts a local FastAPI server and opens the app in the system browser
- persistent data is stored under `%LOCALAPPDATA%\AIGroupDiscovery\data`
- if an older EXE or local checkout already has a `data` folder, that legacy data can be migrated automatically on first packaged launch
