@echo off
setlocal enabledelayedexpansion

set KEY=
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
        if "%%a"=="PB_ENCRYPTION_KEY" set KEY=%%b
        if not defined KEY if "%%a"=="TELEGRAM_API_HASH" set KEY=%%b
    )
)

if not defined KEY (
    set /p KEY="Enter Encryption Key (PB_ENCRYPTION_KEY or TELEGRAM_API_HASH): "
) else (
    echo [i] Using encryption key from .env
)

echo [i] Fetching latest encrypted database from pocketbase-data branch...
git fetch origin +pocketbase-data:pocketbase-data
git show pocketbase-data:data.db.enc > data.db.enc 2>nul

if not exist "data.db.enc" (
    echo [!] Could not retrieve data.db.enc from pocketbase-data branch.
    pause
    exit /b 1
)

if not exist "pb_data" mkdir pb_data

echo [i] Decrypting database...
openssl enc -d -aes-256-cbc -pbkdf2 -iter 100000 -in data.db.enc -out pb_data\data.db -k "%KEY%"
if %errorlevel% neq 0 (
    echo [!] Decryption failed! Please verify your password / encryption key.
    del data.db.enc 2>nul
    pause
    exit /b 1
)

del data.db.enc 2>nul
echo [v] Database successfully decrypted to pb_data\data.db!
echo [i] Run start_pocketbase.bat to view it at http://127.0.0.1:8090/_/
pause
