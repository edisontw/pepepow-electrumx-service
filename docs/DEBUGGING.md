# Troubleshooting and Debugging Guide

This document lists common issues, explanation of root causes, and commands to debug them.

---

## 1. `/wallet/` Page Loads Slowly

If the wallet dashboard page or its static files take too long to fetch:
- Verify Nginx is serving the static directory correctly.
- Check disk content and size.
- Inspect Nginx access and error logs.

### Debugging Commands:

```bash
# Check HTTP response headers and latency
curl -I https://light.pepepow.net/wallet/

# Confirm deployment folder size and structure
find /var/www/pepew-light/wallet -maxdepth 2 -type f

# Check active Nginx logs
sudo tail -n 100 /var/log/nginx/pepew-light.access.log
sudo tail -n 100 /var/log/nginx/pepew-light.error.log
```

---

## 2. Logo Does Not Display

If the PEPEW logo fails to render on the page:
- Confirm absolute root paths like `/logo.png` are avoided, as they resolve to `https://light.pepepow.net/logo.png` rather than `https://light.pepepow.net/wallet/logo.png` (or `/wallet/brand/logo.png`).
- Check that the assets are correctly compiled and located under the subfolder.

### Debugging Commands:

```bash
# Search for logo paths in source code
grep -RIn "logo" /home/ubuntu/pepepow-light-wallet/apps/web/src /home/ubuntu/pepepow-light-wallet/apps/web/public

# Verify logo exist in the static deployment location
find /var/www/pepew-light/wallet -iname "*logo*"
```

---

## 3. JS/CSS Static Assets Return 404

If the HTML page loads, but style sheets and scripts return 404:
- Confirm that Vite's base path is set to `/wallet/` during compilation.
- Verify assets exist in the target folder.

### Debugging Commands:

```bash
# Check Vite base path configuration
grep -R "base:" /home/ubuntu/pepepow-light-wallet/apps/web/vite.config.*

# List deployed CSS/JS assets
find /var/www/pepew-light/wallet/assets -maxdepth 1 -type f
```

---

## 4. API CORS Errors in Frontend Console

If the console shows CORS block errors calling the backend:
- The production build must use relative API paths (same-origin relative paths like `/api/wallet/...`).
- Ensure no hardcoded API base URL variables are present.

### Solution:
Check if `VITE_PEPEW_LIGHT_API_BASE_URL` is set in `apps/web/.env.production`. It should be empty (`VITE_PEPEW_LIGHT_API_BASE_URL=`) to default to same-origin relative endpoints.

---

## 5. Wallet Refresh Returns 404

If clicking refresh on paths like `https://light.pepepow.net/wallet/send` or `https://light.pepepow.net/wallet/history` returns Nginx 404:
- Make sure Nginx uses the correct fallback rule `try_files $uri $uri/ /wallet/index.html` to direct React Router requests back to the entry SPA file.

### Nginx Rule Configuration Check:

```nginx
location /wallet/ {
    root /var/www/pepew-light;
    try_files $uri $uri/ /wallet/index.html;
}
```

---

## 6. API Timeout or ElectrumX Gateway Errors

If the wallet shows API unavailable errors:
- Verify the FastAPI backend is running and ElectrumX status is healthy.
- Inspect FastAPI logs to check if upstream timeout triggers.

### Debugging Commands:

```bash
# Check backend health and status endpoints
curl -s https://light.pepepow.net/api/status

# Check systemd logs for FastAPI
journalctl -u pepew-light.service -n 100 --no-pager
```

---

## 7. Permission Denied (HTTP 500 / 403)

If you see an HTTP 500 or permission denied error when loading the wallet:
- Verify that the Nginx user (`www-data`) has read permissions on all files and execute (traversal) permissions on directories.

### Diagnostics:
```bash
# Check directory chain permissions
ls -ld /var/www /var/www/pepew-light /var/www/pepew-light/wallet

# Trace full path permissions step-by-step
sudo namei -l /var/www/pepew-light/wallet/index.html
```

### Correct Permissions Guideline:
- Directories should be `755` (`rwxr-xr-x`)
- Files should be `644` (`rw-r--r--`)
- Owner should be `www-data:www-data` (or `root:www-data`)

---

## 8. Mixed-language UI text

If the wallet displays English and Chinese at the same time, search for hard-coded strings in:

```bash
grep -RIn "助記詞\|私鑰\|公開測試\|非託管\| / " /home/ubuntu/pepepow-light-wallet/apps/web/src
```

Fix components to use `i18n.t(...)` keys instead of inline bilingual strings.
