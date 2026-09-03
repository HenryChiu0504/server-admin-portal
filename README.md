# Server Admin Portal

Server Admin Portal is a lightweight web-based administration interface for Ubuntu Server. It provides centralized system monitoring and common server administration functions through a responsive web interface.

The project is designed for NVIDIA GPU workstations and multi-GPU servers, and supports deployment behind a reverse proxy using a URL prefix such as `/tool/`.

## Features

- Real-time CPU and system memory monitoring
- Dynamic NVIDIA GPU discovery
- Per-GPU temperature, fan speed, VRAM, and utilization monitoring
- NVIDIA GPU automatic and manual fan control
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
git clone https://github.com/YOUR_USERNAME/server-admin-portal.git
cd server-admin-portal
sudo ./install.sh
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
sudo ./update.sh
```

Alternatively:

```bash
cd /opt/server-admin-portal
git pull
sudo systemctl restart server-admin-portal
```

## Uninstallation

```bash
cd /opt/server-admin-portal
sudo ./uninstall.sh
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
