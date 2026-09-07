#!/bin/sh
# Certbot deploy hook: load a renewed certificate only after configuration validates.
set -eu
/usr/sbin/nginx -t
/bin/systemctl reload nginx
