# Shisho Service Setup

Instructions for setting up and managing the Shisho Discord bot as a systemd service.

## Installation

1.  **Copy the service file to the systemd directory:**
    ```bash
    sudo cp shisho.service /etc/systemd/system/
    ```

2.  **Reload systemd to recognize the new service:**
    ```bash
    sudo systemctl daemon-reload
    ```

3.  **Enable the service to start at boot:**
    ```bash
    sudo systemctl enable shisho.service
    ```

4.  **Start the service now:**
    ```bash
    sudo systemctl start shisho.service
    ```

## Management

- **Check Status:**
  ```bash
  sudo systemctl status shisho.service
  ```

- **Restart Service:**
  ```bash
  sudo systemctl restart shisho.service
  ```

- **Stop Service:**
  ```bash
  sudo systemctl stop shisho.service
  ```

- **View Logs (Real-time):**
  ```bash
  journalctl -u shisho.service -f
  ```

- **Disable Auto-start:**
  ```bash
  sudo systemctl disable shisho.service
  ```
