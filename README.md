# OCI Always Free VM Capacity Grabber & Network Automator

This utility helps you automatically claim and provision Oracle Cloud Infrastructure (OCI) "Always Free" instances (specifically the high-performance Ampere ARM `VM.Standard.A1.Flex` shape, featuring 4 OCPUs and 24 GB of RAM) by continuously retrying launch requests across multiple availability domains until capacity becomes available.

It is designed to run in the background (as a daemon process) on your system, and it is fully optimized to prepare and provision the VM specifically for hosting **OpenClaw** server instances.

---

## Key Features

- **Availability Domain Cycling**: Automatically rotates retry attempts across multiple ADs (e.g. `AD-1`, `AD-2`, `AD-3`) to increase your probability of acquiring capacity.
- **Zero-Config Network Provisioning**: If no active subnets are found, the script automatically provisions:
  - Virtual Cloud Network (VCN): `OpenClawVCN` (`10.0.0.0/16`)
  - Subnet: `OpenClawSubnet` (`10.0.0.0/24`) with public IP support.
  - Internet Gateway: `OpenClawIG` for public routing.
  - Route Table: `OpenClawRouteTable` routing all default traffic (`0.0.0.0/0`) via the gateway.
  - Security List: `OpenClawSecurityList` allowing ingress on port `22` (SSH), ports `80`/`443` (HTTP/HTTPS), and port `18789` (default port for the OpenClaw Dashboard and Gateway).
- **Auto-Generated SSH Keys**: If no local SSH key pair is found on your system, it automatically generates a new key pair (`~/.ssh/oci_key`) and configures it on the VM.
- **Native Background Runner**: Spawns and manages the retry loop as a background daemon process (using native `start`, `stop`, and `status` commands) so you can close your terminal.
- **macOS System Notifications**: Dispatches native desktop alerts, speech synthesis announcements, and system alerts when the VM is successfully created.

---

## Installation & Setup

### 1. Configure OCI API Key
The script authenticates using the official OCI SDK API configuration.
1. Sign in to your [Oracle Cloud Console](https://cloud.oracle.com/).
2. Navigate to your **User Settings** (click your profile icon in the top right).
3. Select **API Keys** under the **Resources** menu on the left, then click **Add API Key**.
4. Choose **Generate API Key Pair**, download the Private Key (PEM file), and click **Add**.
5. Copy the configuration file snippet shown in the console.
6. Create the directory `~/.oci` if it doesn't exist:
   ```bash
   mkdir -p ~/.oci
   ```
7. Paste the snippet into `~/.oci/config` and modify the `key_file` path to point to the absolute path of the downloaded PEM key.
8. Secure the files:
   ```bash
   chmod 600 ~/.oci/config
   chmod 600 /path/to/your-key.pem
   ```

---

## Usage

### 1. Auto-Configure & Provision Network
Generate your `config.json` file automatically. The helper will scan your OCI account and auto-provision the VCN/Subnet and SSH keys if missing:
```bash
python3 -c "import oci" # verifying OCI SDK installation
```
You can run the interactive setup:
```bash
python3 oci_helper.py configure
```
Alternatively, our discovery runner will automatically set up `config.json` for you when running or configuring.

### 2. Manage the Background Retry Loop
Manage the background retry daemon easily with standard subcommands:

- **Start Retrying**:
  ```bash
  python3 oci_helper.py start
  ```
- **Check Status & View Real-time Logs**:
  ```bash
  python3 oci_helper.py status
  ```
- **Stop the Loop**:
  ```bash
  python3 oci_helper.py stop
  ```

Once the VM is successfully created, the background process terminates cleanly, removes its state PID file, and sounds a system chime on your Mac.

---

## File Structure
- `oci_helper.py`: Main execution script containing CLI entry points and loop logic.
- `config.json`: Stores target shape, subnet, image, AD configs, and keys. (Ignored by Git).
- `oci_helper.log`: Real-time execution logs. (Ignored by Git).
- `oci_helper.pid`: Active process ID tracking for background daemonization. (Ignored by Git).
- `.gitignore`: Ensures credentials and system logs are never pushed to GitHub.
