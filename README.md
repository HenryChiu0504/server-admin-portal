# Server Admin Portal

Server Admin Portal is a lightweight web-based administration interface for Ubuntu Server. It provides centralized system monitoring and common server administration functions through a responsive web interface.

The project is designed for NVIDIA GPU workstations and multi-GPU servers, and supports deployment behind a reverse proxy using a URL prefix such as `/tool/`.

## Features

- Real-time CPU and system memory monitoring
- Dynamic NVIDIA GPU discovery
- Per-GPU temperature, fan speed, VRAM, and utilization monitoring
- NVIDIA GPU fan control: Auto, Manual, or a temperature curve
- One-click NVIDIA fan-control recovery
- Browser-based terminal (TTY) with per-user Linux login
- Web file manager (WinSCP-like) with per-user Linux login: browse, upload, download (folders as zip), edit, progress bars
- One-click apt package update
- About page with GitHub link and one-click Portal self-update
- Light and dark themes (follows the OS by default; switch in the top bar)
- GPU processes: who runs what on each GPU (user, VRAM, runtime, Docker/K8s pod), with end-process
- Metric history charts (GPU temperature / utilization / fan / VRAM, CPU / memory), kept 7 days
- Disk space per disk and per user on each disk
- Tailscale installation, authentication, status, and logout
- Linux user creation, deletion, UID/GID display, and optional password reset
- Web administrator password management
- Reverse proxy deployment under `/tool/`

System telemetry is refreshed every three seconds by default. GPU monitoring adapts automatically to the number of NVIDIA GPUs installed in the system.

## Requirements

- Ubuntu Server
- Python 3
- systemd
- Nginx or another reverse proxy
- NVIDIA driver and `nvidia-smi` for GPU monitoring
- Tailscale for Tailscale integration

Installation requires root privileges.

## Installation

Clone the repository and run the installer:

```bash
git clone https://github.com/HenryChiu0504/server-admin-portal.git
cd server-admin-portal
sudo bash install.sh
```

The installer creates the Python environment, installs dependencies, configures the systemd service, and generates the initial administrator credentials.

Application configuration is stored outside the repository at:

```text
/etc/server-admin-portal.env
```

The initial administrator password is displayed in the terminal after the first installation.

## Configuration

Example configuration:

```text
ADMIN_PASSWORD=<generated-password>
SESSION_SECRET=<generated-secret>
PORT=8787
BASE_PATH=/tool
DEFAULT_LINUX_PASSWORD=
```

`ADMIN_PASSWORD` and `SESSION_SECRET` are generated locally during installation and must not be committed to version control.

### Default Linux User Password

`DEFAULT_LINUX_PASSWORD` is optional and intentionally unset in the public distribution. When configured, the user management interface enables the password reset-to-default function.

Configure the value locally in `/etc/server-admin-portal.env`:

```text
DEFAULT_LINUX_PASSWORD=<your-default-password>
```

Restart the service after changing the configuration:

```bash
sudo systemctl restart server-admin-portal
```

## Reverse Proxy

The application backend listens on `127.0.0.1:8787` and is intended to be accessed through a reverse proxy.

To deploy the application under `/tool/`, set:

```text
BASE_PATH=/tool
```

and configure the web server to forward `/tool/` to the application backend.

### Docker Apache Integration

A helper script is included for environments using a Docker-based Apache frontend:

```bash
sudo /opt/server-admin-portal/tools/setup-docker-apache-tool.sh
```

The default Apache container name is `k8s-webserver`. A different container can be specified with:

```bash
sudo APACHE_CONTAINER=my-webserver \
  /opt/server-admin-portal/tools/setup-docker-apache-tool.sh
```

For other reverse proxy configurations, forward `/tool/` to the local application backend or the configured Nginx endpoint.

## Direct Access

To run without a URL prefix, set:

```text
BASE_PATH=
```

and restart the service:

```bash
sudo systemctl restart server-admin-portal
```

## Updating

For Git-based installations:

```bash
cd /opt/server-admin-portal
sudo bash update.sh
```

Alternatively:

```bash
cd /opt/server-admin-portal
git pull
sudo systemctl restart server-admin-portal
```

From the web UI: **關於 → 檢查更新** compares the installation with GitHub. If a newer version exists it
runs `git pull` and `update.sh` in the background (via `systemd-run`, since `update.sh` restarts the Portal)
and streams the log; otherwise it reports that the Portal is already up to date. This requires a Git-based
installation. Log: `/var/log/server-admin-portal/portal-update.log`.

`update.sh` sets `core.fileMode false` so the `chmod +x` it applies to scripts never blocks the next pull.

## Uninstallation

```bash
cd /opt/server-admin-portal
sudo bash uninstall.sh
```

The application directory and `/etc/server-admin-portal.env` are preserved by default to prevent accidental loss of configuration.

## Security

Server Admin Portal performs privileged system administration operations. Deploy it only on trusted networks or behind appropriate access controls.

Recommended practices include:

- Restricting access to trusted LAN or VPN clients
- Using HTTPS for network-accessible deployments
- Using strong administrator and Linux user passwords
- Keeping `/etc/server-admin-portal.env` outside version control
- Reviewing the service configuration before Internet-facing deployment

See [SECURITY.md](SECURITY.md) for additional security information.

## License

No open-source license is currently included. Add an appropriate `LICENSE` file before distributing the project under specific reuse or redistribution terms.


## v2.9.0 — 檔案管理預覽、檢視方式、排序與搜尋

- Clicking a file now **opens a preview** instead of downloading it. Images, PDF, video and audio show inline. Text
  files and logs show in a viewer that can jump between the start and end of the file, auto-refresh every 3 s
  (to follow a running training log), and toggle line wrap. `.log`/`.out`/`.err` files and files over 512 KB open at
  the end. ←/→ steps through the folder, and the viewer has buttons to download or edit the file.
- **View modes**: list, medium icons and large icons, with image thumbnails. Choose sorting by name (natural order:
  1, 2, 10), modified time, size or type, either from the column headers or the sort menu. Also added a toggle for
  hidden files. View and sort choices are remembered in the browser.
- **Search**: typing filters the current folder. Enter searches file and folder names in all subfolders; the search
  stops after 300 results or 20 s. Click the path bar to type a path directly.
- Safety: previews are served by `/api/files/raw`, still running as the signed-in user. Only image, PDF and media
  types are shown inline. Everything else (including `.html` and `.svg` scripts) is sent as a download, with
  `Content-Security-Policy: sandbox` and `nosniff`, so user files never run as pages on the Portal. Range requests
  are supported for video seeking.

## v2.8.0 — 檔案管理

- **檔案管理** page: sign in with a Linux account (PAM, `/etc/pam.d/server-admin-portal`), then browse, create
  folders/files, rename, delete, edit text files (≤ 2 MB, UTF-8, Ctrl+S), upload (multiple files, drag and drop)
  and download. Folders or multi-selections download as one zip.
- Every file operation runs **as the signed-in user** (`setpriv` + `app/fileops.py`, passed to `/usr/bin/python3 -c`),
  so the kernel enforces that user's permissions even though the Portal runs as root. root and system accounts
  (UID < 1000) cannot sign in; 5 wrong passwords lock the address for 1 minute. File-manager logins use their own
  cookie and expire after 8 idle hours.
- Progress bars: uploads go in 8 MB chunks (no proxy size limits; failed chunks retry), zips are built on the server
  with progress (temporary file in `/var/lib/server-admin-portal/zip-tmp`, removed after download or 1 hour; refused
  when the system disk lacks space), and downloads show progress. Chrome/Edge write straight to disk; other browsers
  keep files up to 1.5 GB in memory and hand larger ones to the browser's own downloader.
- nginx streams `/api/files/` without buffering. New dependency: `python-pam` (installed by `update.sh`).

## v2.7.0

- **溫度曲線** fan mode: every 10 s all fans follow the hottest GPU along an editable curve (presets 安靜 / 標準 /
  強冷). Speed rises immediately and falls at most 5% per check; if temperatures cannot be read 3 times in a
  row, fans go to 95%. Stored in `/var/lib/server-admin-portal/fan-curve.json`; Auto / Manual turn it off.
- Dashboard: update banner, GPU usage, fan, disk, package, Tailscale and user cards with warning badges.
- `update.sh` fixes the `After=multi-user.target` ordering of existing `nvidia-fan-x.service` installs (takes
  effect at next boot; the running fan service is not restarted).
- Blocking system calls no longer stall the backend: such endpoints run in the thread pool, so a slow request
  (Tailscale, apt, git) does not delay monitoring or the web terminal.
- Fan page: a mode picked but not yet applied is no longer reset by the 5-second refresh.

## v2.6.0

- **GPU 程序**: every compute process per GPU with its Linux user, VRAM, runtime, command, and Docker / K8s
  pod name; per-user totals; admins can send SIGTERM, then SIGKILL if the process ignores it. Only PIDs that
  are currently on a GPU can be signalled.
- **歷史圖表**: a background thread samples GPU temperature, utilization, fan, VRAM, CPU and memory every
  minute into `/var/lib/server-admin-portal/metrics.db` (SQLite, kept 7 days). Ranges: 1 h / 6 h / 24 h / 7 d.
  History starts when this version is deployed.
- **硬碟空間**: free space per disk, and space used per user on each disk. The per-user scan
  (`find -xdev` under `ionice -c3 nice -n19`) runs every night at 03:00 and on demand; results are cached in
  `/var/lib/server-admin-portal/disk-usage.json`.
- Static assets carry a content-hash `?v=` so browsers load new CSS/JS right after an update.

- Refreshed UI: one token-based stylesheet, sidebar icons, temperature-coloured GPU rings (amber ≥ 70 °C, red ≥ 85 °C).
- Light / dark theme switch (自動 → 淺色 → 深色), remembered per browser; also on the login page.
- The layout now follows the real height of the top telemetry bar, so page headings are never hidden under it.

## v2.4.0

- **關於** page: GitHub URL, current version, and **檢查更新** (one-click self-update from GitHub).
- `update.sh` ignores file-mode changes and works when the checkout is owned by another user.

## v2.3.0

- **Fan-control recovery** (GPU 風扇 → 一鍵恢復風扇控制, `tools/fan_recover.sh`). Fixes the case where
  `plymouth-quit-wait.service` never finishes, `multi-user.target` stays waiting, and
  `nvidia-fan-x.service` (previously ordered `After=multi-user.target`) sits in `start waiting`, so the
  private `:99` X server never starts and `nvidia-fanctl` reports `no GPU targets detected`. The script
  stops the stuck Plymouth job, removes the `After=multi-user.target` ordering, cancels the waiting job,
  clears stale X locks, restarts the service, verifies GPU/fan targets and `GPUFanControlState`, and
  switches fans back to Auto. `fan_install.sh` now writes the corrected unit ordering.
- **Web terminal** (終端機 TTY). xterm.js over a WebSocket to `/bin/login` on a server-side pty; every
  person signs in with their own Linux account. Requires WebSocket proxying: re-run `update.sh` (nginx)
  and, for Docker Apache, `tools/setup-docker-apache-tool.sh` (enables `proxy_wstunnel`).
  The browser loads xterm.js from cdn.jsdelivr.net.
- **One-click package update** (套件更新). Runs `apt-get update && apt-get upgrade` through
  `systemd-run` so closing the page never interrupts dpkg. NVIDIA driver packages are held during the
  upgrade by default, because upgrading a loaded driver breaks `nvidia-smi` and fan control until reboot.
  Log: `/var/log/server-admin-portal/pkg-upgrade.log`.

## v2.2.0-public

- NVIDIA Fan Control readiness now verifies `GPUFanControlState`, not only GPU/Fan target enumeration.
- Avoids taking over an unrelated occupied `:99`; automatically selects a free private display from `:99` to `:95`.
- Persists the selected display in `/etc/server-admin-portal.env` as `NVIDIA_FAN_DISPLAY`.
- Adds Ubuntu 26.04 (`resolute`) package-path compatibility.
- Does not request sudo passwords in the web UI; the Portal backend already performs system-management actions as root.
