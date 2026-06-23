# Hardening notes

- Keep ElectrumX bound to localhost.
- Expose only Nginx HTTPS to the public internet.
- Rate-limit `/api/`.
- Do not log wallet secrets; the server must never receive them.
- Use logrotate for Nginx and systemd logs.
