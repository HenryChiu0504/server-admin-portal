# Security notes

This portal performs privileged server administration tasks and its systemd service runs as `root`.

- Do not commit `/etc/server-admin-portal.env`, real passwords, tokens, private keys, or session secrets.
- Prefer LAN/Tailscale access. Do not expose the portal directly to the public Internet without additional access controls and HTTPS.
- `DEFAULT_LINUX_PASSWORD` is intentionally unset by default. If password reset-to-default functionality is required, configure it locally in `/etc/server-admin-portal.env` and do not commit the value.
- Review changes before running `install.sh` or `update.sh` as root.
