#!/usr/bin/env bash
# Wrapper script to start Admin with Docker group access
exec sg docker -c "$NANOBOT_BIN softnix-admin --host $ADMIN_HOST --port $ADMIN_PORT"
