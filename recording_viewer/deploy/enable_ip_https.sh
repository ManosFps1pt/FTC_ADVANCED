#!/usr/bin/env bash
# Issue a trusted short-lived HTTPS certificate directly for this server's IP,
# protect the entire viewer with HTTP Basic Auth, and renew twice each day.
set -euo pipefail

public_ip="${1:?Usage: enable_ip_https.sh <public-ip> [username]}"
viewer_username="${2:-ftcviewer}"
if [[ ! "$public_ip" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}$ ]]; then
  echo "Expected an IPv4 address" >&2
  exit 2
fi

sudo apt-get update -qq
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq apache2-utils
if ! command -v certbot >/dev/null; then
  sudo snap install --classic certbot
  sudo ln -sfn /snap/bin/certbot /usr/local/bin/certbot
fi

sudo install -d -m 0755 /var/www/ftc-recording-viewer-acme
sudo install -m 0644 /opt/ftc-recording-viewer/recording_viewer/deploy/nginx-http-bootstrap.conf /etc/nginx/sites-available/ftc-recording-viewer
sudo nginx -t
sudo systemctl reload nginx

# IP-address certificates use the short-lived profile.  The cron entry below
# renews well before their six-day lifetime ends.  No email is registered, so
# operational status is verified by the renewal command instead.
sudo certbot certonly --non-interactive --agree-tos --register-unsafely-without-email \
  --preferred-profile shortlived --webroot --webroot-path /var/www/ftc-recording-viewer-acme \
  --ip-address "$public_ip"

viewer_password="$(openssl rand -base64 24 | tr -d '\n')"
printf '%s\n' "$viewer_password" | sudo htpasswd -i -B -c /etc/nginx/.ftc-recording-viewer.htpasswd "$viewer_username"
sudo chmod 0640 /etc/nginx/.ftc-recording-viewer.htpasswd
sudo chown root:www-data /etc/nginx/.ftc-recording-viewer.htpasswd
sudo sed "s/__PUBLIC_IP__/$public_ip/g" /opt/ftc-recording-viewer/recording_viewer/deploy/nginx-https-ip.conf | sudo tee /etc/nginx/sites-available/ftc-recording-viewer >/dev/null
sudo nginx -t
sudo systemctl reload nginx
sudo ufw allow 443/tcp
printf '%s\n' '17 */12 * * * root /snap/bin/certbot renew --quiet --preferred-profile shortlived' | sudo tee /etc/cron.d/ftc-recording-viewer-certbot >/dev/null
sudo chmod 0644 /etc/cron.d/ftc-recording-viewer-certbot
echo "FTC_VIEWER_USERNAME=$viewer_username"
echo "FTC_VIEWER_PASSWORD=$viewer_password"
echo "FTC_VIEWER_URL=https://$public_ip/"
