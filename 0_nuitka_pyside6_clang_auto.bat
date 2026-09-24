@echo off
rem ==========================================================================
rem  Excel_Manager packaging script (Nuitka + PySide6 plugin)
rem
rem  Notes (KEEP THIS FILE ASCII-ONLY, CRLF, NO BOM: cmd parses byte by byte,
rem  a mid-file chcp together with UTF-8 comments broke line splitting before):
rem   1) cd to this script's folder, so it builds no matter where it is run from
rem   2) refuse to build while Excel_Manager.exe is running (Nuitka cannot
rem      overwrite the exe; otherwise the link step dies with
rem      "scons: *** ... Error 1")
rem   3) dry run / self check:  set EM_SKIP_BUILD=1  then run this script
rem   4) when the app gains new data files or dependencies, update the
rem      --include-data-files / --include-module lines below in the SAME change
rem ==========================================================================
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-Process -Name 'Excel_Manager' -ErrorAction SilentlyContinue) { exit 1 } else { exit 0 }"
if errorlevel 1 (
    echo [ERROR] Excel_Manager.exe is still running. Close it first, then build again.
    exit /b 1
)

if not exist "icon.ico" (
    echo [ERROR] icon.ico not found in "%~dp0"
    exit /b 1
)

if defined EM_SKIP_BUILD (
    echo [dry-run] checks passed. Unset EM_SKIP_BUILD to build for real.
    exit /b 0
)

nuitka --standalone ^
    --enable-plugin=pyside6 ^
    --windows-console-mode=disable ^
    --assume-yes-for-downloads ^
    --windows-icon-from-ico=icon.ico ^
    --include-data-files=icon.ico=icon.ico ^
    --include-module=chardet ^
    --include-module=pypinyin ^
    --include-module=openpyxl ^
    --include-module=barcode ^
    --include-module=qrcode ^
    --include-module=PIL ^
    --include-package-data=barcode ^
    --include-package-data=qrcode ^
    --include-package-data=PIL ^
    --include-package-data=pypinyin ^
    --follow-imports ^
    --jobs=4 ^
    --clang ^
    --output-dir=build_output ^
    --remove-output ^
    Excel_Manager.py
