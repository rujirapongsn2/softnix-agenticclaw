#!/bin/bash
# Wrapper script to start Admin with Docker group access
# This ensures the Admin service can access Docker socket for sandbox operations

# Add docker group to current session
exec sg docker -c "/home/rujirapong/softnix-agenticclaw/.venv/bin/nanobot softnix-admin --host 0.0.0.0 --port 18880"
