# Disable Client Token Authentication

## Goal

Allow the Open-XiaoAI Rust Client on the trusted home LAN speaker at
`10.0.0.5` to connect to the Bridge at `10.0.0.22:4399` without modifying or
rebuilding the Client.

## Current state

- `open-xiaoai-bridge.service` is active and listens on `0.0.0.0:4399`.
- The service loads configuration from
  `/home/torrey/.config/open-xiaoai-bridge/environment`.
- `OPEN_XIAOAI_TOKEN` is set, so the Bridge requires an
  `Authorization: Bearer ...` header.
- The deployed Rust Client does not send that header and is rejected once per
  second with an authentication failure.
- The speaker and Bridge are on a trusted home LAN.

## Design

1. Create a permission-preserving backup of the systemd environment file.
2. Remove only the `OPEN_XIAOAI_TOKEN` assignment from the active environment
   file. Preserve every other variable and retain mode `0600`.
3. Restart `open-xiaoai-bridge.service` so the process receives the updated
   environment.
4. Leave the Client binary and speaker startup configuration unchanged.

With `OPEN_XIAOAI_TOKEN` unset, `AppServer::connect` follows its existing
unauthenticated WebSocket path. No source-code changes are required.

## Validation

- Confirm the systemd service is active after restart.
- Confirm ports 4399 and 9092 are listening.
- Confirm the speaker Client remains running.
- Confirm `/tmp/client_log.txt` on the speaker reports a successful connection.
- Confirm Bridge logs report the Client connection and no new authentication
  failures from `10.0.0.5`.

## Rollback

If the service fails to start or the Client does not connect, restore the
environment-file backup, restart the service, and verify the prior authenticated
state is restored.

## Security boundary

After this change, any device that can reach TCP port 4399 on the trusted LAN
can attempt to connect. This design does not expose the Bridge through router
port forwarding and does not alter host firewall rules.
