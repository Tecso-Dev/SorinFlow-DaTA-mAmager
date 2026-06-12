#!/bin/bash
# SSL Certificate Auto-Renewal Script
cd /root/sorinflow-divar-scraper
docker compose stop nginx
certbot renew --quiet
cp /etc/letsencrypt/live/sc.sorinflow.com/fullchain.pem /root/sorinflow-divar-scraper/nginx/ssl/
cp /etc/letsencrypt/live/sc.sorinflow.com/privkey.pem /root/sorinflow-divar-scraper/nginx/ssl/
docker compose start nginx
