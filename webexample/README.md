# Secure Remote TWAIN Scanning Gateway

This example turns the local Dynamic Web TWAIN Service into a shared, network-accessible scanning gateway.

It adds three controls that are missing from a direct LAN-exposed scanner:

- registered user access with password-based sign-in
- JWT-protected API calls for every scan request
- scanner locking so only one user can acquire a device at a time while other users can still see status

https://github.com/user-attachments/assets/aedfa5fa-ffa6-4ac9-9184-01f0ec5a4fd0

## Features

- Hidden-by-default registration and sign-in flows on the public landing view
- Self-service user registration backed by SQLite
- OAuth2 password flow with JWT bearer tokens
- Built-in administrator portal for listing and deleting registered users
- Same-origin web dashboard served by FastAPI
- Explicit scanner lease table with lock owner and expiry tracking
- TWAIN scanner discovery over the local network through the Python SDK
- Centered scan preview with scrollable page viewing and thumbnail navigation
- Scan preview streaming as PNG or JPEG pages
- Export scanned results as PDF or PNG files after each scan

## Prerequisites

- Python 3.10+
- Dynamic Web TWAIN Service running on the machine attached to the scanner
- A license key that includes the REST API module

## Configuration

Copy `.env.example` and set the required values:

```powershell
Copy-Item .env.example .env
```

The app reads `webexample/.env` automatically on startup. Restart the FastAPI process after changing `.env` so the updated values are applied.

Environment variables:

- `DWT_LICENSE_KEY`: Dynamic Web TWAIN license key
- `DWT_SERVICE_HOST`: REST API host, for example `http://192.168.1.20:18622`
- `REMOTE_SCAN_JWT_SECRET`: long random secret used to sign access tokens
- `ACCESS_TOKEN_TTL_MINUTES`: bearer token lifetime
- `SCANNER_LOCK_TTL_SECONDS`: stale lock timeout
- `REMOTE_SCAN_SCANNER_TYPES`: scanner type mask. `0x50` enables TWAIN and TWAIN x64.
- `REMOTE_SCAN_ADMIN_USERNAME`: optional bootstrap administrator username
- `REMOTE_SCAN_ADMIN_PASSWORD`: optional bootstrap administrator password
- `REMOTE_SCAN_ADMIN_FULL_NAME`: display name for the bootstrap administrator account

## Install and Run

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Get-Content .env | ForEach-Object {
  if ($_ -and -not $_.StartsWith('#')) {
    $name, $value = $_.Split('=', 2)
    Set-Item -Path Env:$name -Value $value
  }
}
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://127.0.0.1:8000` in a browser on any machine in the same network.

## User Management

- If `REMOTE_SCAN_ADMIN_USERNAME` and `REMOTE_SCAN_ADMIN_PASSWORD` are set, the app creates or refreshes that administrator account on startup.
- If no bootstrap admin is configured, the first registered account becomes the administrator automatically.
- The landing page shows which account currently has administrator access.
- Administrators can review and delete registered users from the `Registered Users` panel after signing in.
- End users still register from the public landing page, but the registration and sign-in forms stay hidden until a user clicks the corresponding button.

## Scan Output

- Scan results render in a centered preview stage with a scrollable container and page thumbnails.
- `Export PDF` downloads a single PDF containing all captured pages.
- `Export PNG` downloads one PNG for a single-page scan, or a ZIP archive of PNG files for multi-page scans.

## How Locking Works

1. A signed-in user requests a scan for a specific scanner.
2. The gateway writes a scanner lease to SQLite before it starts the job.
3. Other users can still refresh the dashboard and see who owns the device.
4. The gateway creates a pending Dynamic Web TWAIN job, starts it, streams the pages back, then releases the lease.
5. If a client disconnects or a job fails, the lease automatically expires based on `SCANNER_LOCK_TTL_SECONDS`.

## Security Notes

- Keep the FastAPI app and the Dynamic Web TWAIN Service behind HTTPS in production.
- Replace the JWT secret before exposing the gateway outside a trusted network.
- The frontend stores the access token in `sessionStorage`, which reduces persistence across browser restarts.
- The app adds CSP, frame, and MIME-sniffing headers to reduce common browser-side risks.
