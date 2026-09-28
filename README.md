# Server Admin Portal

Server Admin Portal is a lightweight web-based administration interface for Ubuntu Server. It provides centralized system monitoring and common server administration functions through a responsive web interface.

The project is designed for NVIDIA GPU workstations and multi-GPU servers, and supports deployment behind a reverse proxy using a URL prefix such as `/tool/`.

## Features

- Real-time CPU and system memory monitoring
- Dynamic NVIDIA GPU discovery
- Per-GPU temperature, fan speed, VRAM, and utilization monitoring
- NVIDIA GPU automatic and manual fan control
- One-click NVIDIA fan-control recovery
- Browser-based terminal (TTY) with per-user Linux login
- One-click apt package update
- About page with GitHub link and one-click Portal self-update
- Light and dark themes (follows the OS by default; switch in the top bar)
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


## v2.5.0

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
