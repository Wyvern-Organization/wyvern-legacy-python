# Wyvern

Wyvern is currently a web-based Discord Alternative that is semi open-source, and is currently being developed by 3 people.

## Easy startup

- ASPC: `pwsh .\scripts\start-aspc.ps1`
- Nubu: `bash ./scripts/start-nubu.sh`

The ASPC launcher starts the backend on port `8009` and brings up its Cloudflared tunnel.
The Nubu launcher starts the backend on port `8000`, then starts Cloudflared first and the Code Tunnels fallback alongside it.
