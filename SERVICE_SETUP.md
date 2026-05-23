# Shisho Service Setup

Instructions for setting up and managing the Shisho Discord bot as a systemd service on a Proxmox Arch container (running as root).

## Installation (Automated)

The easiest way to set everything up is using the provided script:

```bash
chmod +x setup_service.sh
./setup_service.sh
```

## Installation (Manual)

If you prefer to do it manually:

1.  **Sync the environment:**
    `bash
uv sync
`
    ...

2.  **Copy the service file to the systemd directory:**

    ```bash
    cp shisho.service /etc/systemd/system/
    ```

3.  **Reload systemd to recognize the new service:**

    ```bash
    systemctl daemon-reload
    ```

4.  **Enable the service to start at boot:**

    ```bash
    systemctl enable shisho.service
    ```

5.  **Start the service now:**
    ```bash
    systemctl start shisho.service
    ```

## Management

- **Update Shisho:**
  Pulls the latest code from GitHub, syncs dependencies, and restarts the service.

  ```bash
  chmod +x update_shisho.sh
  ./update_shisho.sh
  ```

- **Check Status:**
  ...

  ```bash
  systemctl status shisho.service
  ```

- **Restart Service:**

  ```bash
  systemctl restart shisho.service
  ```

- **Stop Service:**

  ```bash
  systemctl stop shisho.service
  ```

- **View Logs (Real-time):**

  ```bash
  journalctl -u shisho.service -f
  ```

- **Disable Auto-start:**
  ```bash
  systemctl disable shisho.service
  ```
