#!/usr/bin/env python3
"""
OCI Always Free Capacity Retry Grabber
Natively handles auto-provisioning of network resources, SSH key pair generation,
and daemonized background loops to continuously retry VM creation until successful.
"""

import os
import sys
import json
import time
import argparse
import subprocess
from typing import Any, Callable, Dict, List, Optional, Union

# ==============================================================================
# Dependency Management
# ==============================================================================
def install_dependencies() -> None:
    """Ensures the OCI SDK dependency is installed automatically on first run."""
    try:
        import oci  # noqa: F401
    except ImportError:
        print("OCI SDK not found. Installing requirements...")
        try:
            script_dir = os.path.dirname(os.path.abspath(__file__))
            req_path = os.path.join(script_dir, "requirements.txt")
            if os.path.exists(req_path):
                subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_path])
            else:
                subprocess.check_call([sys.executable, "-m", "pip", "install", "oci"])
            print("Successfully installed dependencies!\n")
        except Exception as e:
            print(f"Error installing dependencies automatically: {e}")
            print("Please install the OCI SDK manually by running: pip3 install oci")
            sys.exit(1)

# Ensure dependencies are loaded
install_dependencies()
import oci  # type: ignore

# ==============================================================================
# Helper Utilities
# ==============================================================================
def select_from_menu(prompt: str, items: List[Any], formatter: Callable[[Any], str] = str) -> Any:
    """Displays a numbered list of items and prompts the user to select one."""
    if not items:
        print("No items available.")
        return None
    
    print(f"\n{prompt}:")
    for i, item in enumerate(items):
        print(f"  [{i + 1}] {formatter(item)}")
    
    while True:
        val = input(f"Select option (1-{len(items)}): ").strip()
        try:
            idx = int(val) - 1
            if 0 <= idx < len(items):
                return items[idx]
        except ValueError:
            pass
        print(f"Invalid choice. Please enter a number between 1 and {len(items)}.")

def list_profiles() -> List[str]:
    """Lists available OCI profiles in ~/.oci/config."""
    config_path = os.path.expanduser("~/.oci/config")
    if not os.path.exists(config_path):
        return []
    profiles: List[str] = []
    try:
        with open(config_path, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("[") and line.endswith("]"):
                    profiles.append(line[1:-1])
    except Exception as e:
        print(f"Warning reading config file profiles: {e}")
    return profiles

def get_default_ssh_keys() -> List[tuple]:
    """Finds existing public SSH keys in ~/.ssh/."""
    ssh_dir = os.path.expanduser("~/.ssh")
    found_keys: List[tuple] = []
    if os.path.exists(ssh_dir):
        try:
            for name in os.listdir(ssh_dir):
                if name.endswith(".pub"):
                    full_path = os.path.join(ssh_dir, name)
                    found_keys.append((name, full_path))
        except Exception:
            pass
    return found_keys

def notify_success(display_name: str) -> None:
    """Dispatches asynchronous system notifications and sounds on macOS."""
    title = "OCI VM Provisioned!"
    message = f"VM '{display_name}' has been created successfully!"
    print(f"\n📢 {message}")
    
    # macOS system notification banner (asynchronous)
    try:
        cmd = f'osascript -e \'display notification "{message}" with title "{title}"\''
        subprocess.Popen(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    
    # macOS speech synthesis (asynchronous)
    try:
        subprocess.Popen(f'say "{title} Oracle Cloud instance is now ready."', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

    # Play macOS Glass sound (asynchronous)
    try:
        subprocess.Popen("afplay /System/Library/Sounds/Glass.aiff", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
        
    # Terminal bell sounds
    for _ in range(5):
        sys.stdout.write('\a')
        sys.stdout.flush()
        time.sleep(0.2)

# ==============================================================================
# Configure Subcommand
# ==============================================================================
def run_configure(args: argparse.Namespace) -> None:
    """Executes the interactive Wizard configuration to generate config.json."""
    print("="*60)
    print("        OCI Instance Creator - Configuration Wizard")
    print("="*60)
    
    config_path = os.path.expanduser(args.oci_config)
    if not os.path.exists(config_path):
        print(f"\n❌ Error: OCI config file not found at {config_path}")
        print("Please set up your OCI API keys and credentials first.")
        print("See the README.md instructions to create ~/.oci/config.")
        sys.exit(1)
        
    profiles = list_profiles()
    selected_profile = "DEFAULT"
    if len(profiles) > 1:
        selected_profile = select_from_menu("Select OCI Profile", profiles)
    elif len(profiles) == 1:
        selected_profile = profiles[0]
        print(f"Using the only profile found: '{selected_profile}'")
    else:
        print(f"No profiles found in {config_path}. Defaulting to 'DEFAULT'.")
        
    print(f"\nLoading configuration for profile '{selected_profile}'...")
    try:
        config = oci.config.from_file(config_path, selected_profile)
    except Exception as e:
        print(f"❌ Error loading profile '{selected_profile}': {e}")
        sys.exit(1)
        
    # Authenticate and initialize identity client
    try:
        identity_client = oci.identity.IdentityClient(config)
        tenancy = identity_client.get_tenancy(config["tenancy"]).data
        print(f"Successfully authenticated as Tenancy: {tenancy.name}")
    except Exception as e:
        print(f"❌ OCI Authentication failed: {e}")
        print("Please check your ~/.oci/config details and API Key file path.")
        sys.exit(1)
        
    # 1. Compartment selection
    print("\nFetching compartments...")
    all_compartments = [{"id": config["tenancy"], "name": f"Root Tenancy ({tenancy.name})"}]
    try:
        compartments = identity_client.list_compartments(
            compartment_id=config["tenancy"],
            compartment_id_in_subtree=True,
            access_level="ACCESSIBLE"
        ).data
        for c in compartments:
            if c.lifecycle_state == "ACTIVE":
                all_compartments.append({"id": c.id, "name": c.name})
    except Exception as e:
        print(f"Warning fetching child compartments: {e}")
        print("Using Root Tenancy compartment by default.")
        
    selected_compartment = select_from_menu(
        "Select Compartment",
        all_compartments,
        formatter=lambda x: x["name"]
    )
    compartment_id = selected_compartment["id"]

    # 2. Availability Domain selection (support all or single ADs)
    print("\nFetching Availability Domains...")
    try:
        ads = identity_client.list_availability_domains(compartment_id=compartment_id).data
    except Exception as e:
        print(f"❌ Error fetching ADs: {e}")
        sys.exit(1)
        
    ad_options = []
    if len(ads) > 1:
        ad_options.append({
            "name": "All Availability Domains (Cycles through them to find capacity)", 
            "value": [ad.name for ad in ads]
        })
    for ad in ads:
        ad_options.append({"name": ad.name, "value": ad.name})
        
    selected_ad_option = select_from_menu(
        "Select Availability Domain",
        ad_options,
        formatter=lambda x: x["name"]
    )
    availability_domain = selected_ad_option["value"]

    # 3. Shape selection
    shape_options = [
        {"name": "VM.Standard.A1.Flex (Ampere ARM - Always Free up to 2 OCPUs, 12GB for 24/7 instances)", "shape": "VM.Standard.A1.Flex"},
        {"name": "VM.Standard.E2.1.Micro (AMD x86 - Always Free 1 OCPU, 1GB)", "shape": "VM.Standard.E2.1.Micro"},
        {"name": "Enter Custom Shape Name", "shape": "custom"}
    ]
    selected_shape_option = select_from_menu(
        "Select Instance Shape",
        shape_options,
        formatter=lambda x: x["name"]
    )
    
    if selected_shape_option["shape"] == "custom":
        shape = input("Enter shape name: ").strip()
        is_flex = input("Is this a Flex shape (supports custom OCPU/Memory)? (y/n): ").strip().lower() == 'y'
    else:
        shape = selected_shape_option["shape"]
        is_flex = shape.endswith(".Flex")

    shape_config = None
    if is_flex:
        print(f"\nConfiguring Flex Shape options for {shape}:")
        while True:
            try:
                ocpus = float(input("Number of OCPUs (default 2.0 for A1 Always Free): ").strip() or "2.0")
                break
            except ValueError:
                print("Please enter a valid decimal number.")
        while True:
            try:
                memory = float(input("Memory in GBs (default 12.0 for A1 Always Free): ").strip() or "12.0")
                break
            except ValueError:
                print("Please enter a valid decimal number.")
        shape_config = {
            "ocpus": ocpus,
            "memory_in_gbs": memory
        }

    # 4. Networking: VCN and Subnet
    vcn_client = oci.core.VirtualNetworkClient(config)
    print("\nFetching Virtual Cloud Networks (VCNs)...")
    try:
        vcns = vcn_client.list_vcns(compartment_id=compartment_id).data
    except Exception as e:
        print(f"❌ Error listing VCNs: {e}")
        sys.exit(1)
        
    if not vcns:
        print("❌ No VCNs found in this compartment.")
        print("Please run our auto-provisioning tool or set up a VCN in OCI console first.")
        sys.exit(1)
        
    selected_vcn = select_from_menu(
        "Select VCN",
        vcns,
        formatter=lambda x: x.display_name
    )
    
    print("\nFetching subnets in selected VCN...")
    try:
        subnets = vcn_client.list_subnets(compartment_id=compartment_id, vcn_id=selected_vcn.id).data
    except Exception as e:
        print(f"❌ Error listing subnets: {e}")
        sys.exit(1)
        
    if not subnets:
        print("❌ No subnets found in the selected VCN.")
        sys.exit(1)
        
    selected_subnet = select_from_menu(
        "Select Subnet",
        subnets,
        formatter=lambda x: f"{x.display_name} ({x.cidr_block})"
    )
    subnet_id = selected_subnet.id

    # 5. OS Image Selection
    compute_client = oci.core.ComputeClient(config)
    print(f"\nFetching OS Images compatible with shape {shape}...")
    images: List[Any] = []
    try:
        images = compute_client.list_images(compartment_id=compartment_id, shape=shape).data
        images = sorted(images, key=lambda x: x.time_created, reverse=True)
    except Exception as e:
        print(f"Warning fetching compatible images: {e}")
        
    if not images:
        print("Could not list shape-specific images. Fetching all active images...")
        try:
            images = compute_client.list_images(compartment_id=compartment_id).data
            images = sorted(images, key=lambda x: x.time_created, reverse=True)
        except Exception as e:
            print(f"❌ Error listing images: {e}")
            sys.exit(1)

    image_menu_items = [{"name": f"{img.display_name} ({img.operating_system} {img.operating_system_version})", "id": img.id} for img in images[:15]]
    image_menu_items.append({"name": "Enter Custom Image OCID manually", "id": "custom"})
    
    selected_image_option = select_from_menu(
        "Select Operating System Image",
        image_menu_items,
        formatter=lambda x: x["name"]
    )
    
    if selected_image_option["id"] == "custom":
        image_id = input("Enter Image OCID: ").strip()
    else:
        image_id = selected_image_option["id"]

    # 6. SSH Authorized Key Setup
    ssh_keys = get_default_ssh_keys()
    ssh_authorized_keys = ""
    if ssh_keys:
        ssh_key_options = [{"name": f"Use local key: {k[0]}", "path": k[1]} for k in ssh_keys]
        ssh_key_options.append({"name": "Paste custom SSH public key string", "path": "custom"})
        ssh_key_options.append({"name": "Provide custom public key file path", "path": "path"})
        
        selected_key_option = select_from_menu(
            "Select SSH Public Key",
            ssh_key_options,
            formatter=lambda x: x["name"]
        )
        
        if selected_key_option["path"] == "custom":
            ssh_authorized_keys = input("Paste SSH Public Key: ").strip()
        elif selected_key_option["path"] == "path":
            file_path = input("Enter path to public key file (e.g., ~/.ssh/my_key.pub): ").strip()
            full_path = os.path.expanduser(file_path)
            try:
                with open(full_path, "r") as f:
                    ssh_authorized_keys = f.read().strip()
            except Exception as e:
                print(f"❌ Error reading file: {e}")
                sys.exit(1)
        else:
            try:
                with open(selected_key_option["path"], "r") as f:
                    ssh_authorized_keys = f.read().strip()
            except Exception as e:
                print(f"❌ Error reading key file: {e}")
                sys.exit(1)
    else:
        print("\nNo local public SSH keys found in ~/.ssh/.")
        ssh_choice = input("Would you like to (1) paste public key string or (2) provide path to file? (1/2): ").strip()
        if ssh_choice == "2":
            file_path = input("Enter path to public key file: ").strip()
            full_path = os.path.expanduser(file_path)
            try:
                with open(full_path, "r") as f:
                    ssh_authorized_keys = f.read().strip()
            except Exception as e:
                print(f"❌ Error reading file: {e}")
                sys.exit(1)
        else:
            ssh_authorized_keys = input("Paste SSH Public Key: ").strip()

    if not ssh_authorized_keys.startswith(("ssh-rsa", "ssh-dss", "ecdsa-sha2", "ssh-ed25519")):
        print("⚠️ Warning: The provided key does not look like a standard SSH public key format.")

    # 7. Display Name and Interval Settings
    display_name = input("\nEnter VM Display Name (default: AlwaysFreeVM): ").strip() or "AlwaysFreeVM"
    
    while True:
        try:
            retry_interval = int(input("Retry interval in seconds (default 60): ").strip() or "60")
            if retry_interval < 5:
                print("Interval must be at least 5 seconds.")
                continue
            break
        except ValueError:
            print("Please enter an integer.")

    # Write Config
    config_output = {
        "profile": selected_profile,
        "compartment_id": compartment_id,
        "availability_domain": availability_domain,
        "shape": shape,
        "shape_config": shape_config,
        "subnet_id": subnet_id,
        "image_id": image_id,
        "display_name": display_name,
        "ssh_authorized_keys": ssh_authorized_keys,
        "retry_interval_seconds": retry_interval
    }

    output_path = os.path.join(os.getcwd(), "config.json")
    try:
        with open(output_path, "w") as f:
            json.dump(config_output, f, indent=4)
        print(f"\n✅ Configuration saved to: {output_path}")
        print("\nYou can now start the retry loop using: python3 oci_helper.py start")
    except Exception as e:
        print(f"❌ Error writing configuration to file: {e}")
        sys.exit(1)

# ==============================================================================
# Run Subcommand
# ==============================================================================
def run_loop(args: argparse.Namespace) -> None:
    """Runs the main retry loop to capture VM capacity (executed in foreground)."""
    print("="*60)
    print("            OCI VM Capacity Retry Launcher")
    print("="*60)
    
    config_path = os.path.join(os.getcwd(), "config.json")
    if not os.path.exists(config_path):
        print(f"❌ Error: Configuration file not found at {config_path}")
        print("Please run the configuration helper first:")
        print("  python3 oci_helper.py configure")
        sys.exit(1)
        
    try:
        with open(config_path, "r") as f:
            config_data = json.load(f)
    except Exception as e:
        print(f"❌ Error reading configuration JSON: {e}")
        sys.exit(1)
        
    oci_creds_path = os.path.expanduser(args.oci_config)
    profile = config_data.get("profile", "DEFAULT")
    
    if args.mock:
        print("⚠️ Running in MOCK Mode! No actual OCI API calls will be made.")
        mock_run(config_data)
        return

    if not os.path.exists(oci_creds_path):
        print(f"❌ OCI credentials config not found at {oci_creds_path}")
        sys.exit(1)

    try:
        oci_config = oci.config.from_file(oci_creds_path, profile)
    except Exception as e:
        print(f"❌ Error loading OCI profile '{profile}' from credentials config: {e}")
        sys.exit(1)

    try:
        compute_client = oci.core.ComputeClient(oci_config)
    except Exception as e:
        print(f"❌ Failed to construct OCI ComputeClient: {e}")
        sys.exit(1)

    # Parse AD configurations (supports string or list for cycling ADs)
    ad_config = config_data["availability_domain"]
    ads = [ad_config] if isinstance(ad_config, str) else ad_config
    
    # Initialize LaunchInstanceDetails
    launch_details = oci.core.models.LaunchInstanceDetails(
        compartment_id=config_data["compartment_id"],
        availability_domain=ads[0],
        shape=config_data["shape"],
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=config_data["subnet_id"],
            assign_public_ip=True
        ),
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=config_data["image_id"],
            source_type="image"
        ),
        display_name=config_data["display_name"],
        metadata={
            "ssh_authorized_keys": config_data["ssh_authorized_keys"]
        }
    )
    
    if config_data.get("shape_config"):
        launch_details.shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=config_data["shape_config"]["ocpus"],
            memory_in_gbs=config_data["shape_config"]["memory_in_gbs"]
        )

    print("\nVM Configuration Loaded:")
    print(f"  Shape:              {config_data['shape']}")
    if config_data.get("shape_config"):
        print(f"  CPU/Memory:         {config_data['shape_config']['ocpus']} OCPUs / {config_data['shape_config']['memory_in_gbs']} GB RAM")
    print(f"  Display Name:       {config_data['display_name']}")
    print(f"  Availability Zone:  {config_data['availability_domain']}")
    print(f"  Subnet ID:          {config_data['subnet_id']}")
    print(f"  Image ID:           {config_data['image_id']}")
    print("-"*60)
    print("Starting retry loop. Press Ctrl+C to cancel.")
    print("-"*60)

    retry_count = 0
    interval = config_data.get("retry_interval_seconds", 60)
    
    while True:
        retry_count += 1
        current_ad = ads[(retry_count - 1) % len(ads)]
        launch_details.availability_domain = current_ad
        
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] Launch Attempt #{retry_count} (AD: {current_ad.split('-')[-1]})...", end="", flush=True)
        
        try:
            response = compute_client.launch_instance(launch_details)
            instance = response.data
            
            print("\n\n" + "*"*60)
            print("🚀 SUCCESS! VM Instance Created Successfully!")
            print(f"  Instance ID:   {instance.id}")
            print(f"  Display Name:  {instance.display_name}")
            print(f"  State:         {instance.lifecycle_state}")
            print("*"*60 + "\n")
            
            notify_success(instance.display_name)
            break
            
        except oci.exceptions.ServiceError as e:
            err_msg = str(e.message or "").lower()
            is_capacity_issue = (
                "capacity" in err_msg or 
                "limit" in err_msg or
                e.status in [429, 500, 503]
            )
            
            if is_capacity_issue:
                print(f" Failed: Capacity Unavailable (HTTP {e.status})")
                print(f"  └─ Message: {e.message.strip()}")
                print(f"  └─ Sleeping for {interval} seconds...")
                time.sleep(interval)
            else:
                print(f"\n\n❌ CRITICAL OCI Service Error (HTTP {e.status})")
                print(f"  Code:    {e.code}")
                print(f"  Message: {e.message}")
                print("\nStopping loop to prevent spamming with incorrect credentials/configurations.")
                sys.exit(1)
                
        except Exception as e:
            print(f"\n\n❌ CRITICAL Unexpected Exception: {e}")
            print("Stopping loop.")
            sys.exit(1)

# ==============================================================================
# Mock / Dry-Run Mode
# ==============================================================================
def mock_run(config_data: Dict[str, Any]) -> None:
    """Simulates a dry run of the retry launcher with mock outcomes."""
    interval = config_data.get("retry_interval_seconds", 5)
    print(f"Simulating OCI VM creation every {interval} seconds...")
    print("Will simulate 2 out-of-capacity failures and then succeed.")
    print("-"*60)
    
    ad_config = config_data["availability_domain"]
    ads = [ad_config] if isinstance(ad_config, str) else ad_config
    
    for i in range(1, 4):
        current_ad = ads[(i - 1) % len(ads)]
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S')
        print(f"[{timestamp}] Launch Attempt #{i} (AD: {current_ad.split('-')[-1]})...", end="", flush=True)
        time.sleep(1.5)
        
        if i < 3:
            print(" Failed: Capacity Unavailable (HTTP 500)")
            print("  └─ Message: Out of host capacity.")
            print(f"  └─ Sleeping for {interval} seconds...")
            time.sleep(interval)
        else:
            print("\n\n" + "*"*60)
            print("🚀 SUCCESS! (MOCK) VM Instance Created Successfully!")
            print("  Instance ID:   ocid1.instance.oc1.mock.createdinstanceocid")
            print(f"  Display Name:  {config_data['display_name']}")
            print("  State:         PROVISIONING")
            print("*"*60 + "\n")
            notify_success(config_data['display_name'])
            break

# ==============================================================================
# Background Runner Subcommands
# ==============================================================================
def run_start(args: argparse.Namespace) -> None:
    """Launches the instance launcher in a fully detached background process."""
    pid_path = os.path.join(os.getcwd(), "oci_helper.pid")
    log_path = os.path.join(os.getcwd(), "oci_helper.log")
    
    config_path = os.path.join(os.getcwd(), "config.json")
    if not os.path.exists(config_path):
        print(f"❌ Error: Configuration file not found at {config_path}")
        print("Please run the configuration helper first:")
        print("  python3 oci_helper.py configure")
        sys.exit(1)
        
    if os.path.exists(pid_path):
        try:
            with open(pid_path, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            print(f"⚠️ Retry loop is already running in the background with PID {pid}.")
            print("Use 'python3 oci_helper.py status' or 'python3 oci_helper.py stop' to manage it.")
            sys.exit(1)
        except (ValueError, OSError):
            try:
                os.remove(pid_path)
            except Exception:
                pass

    print("Starting OCI VM Capacity Retry Loop in the background...")
    
    # Re-run program with 'run' argument, forcing unbuffered binary streams via '-u'
    cmd = [sys.executable, "-u", os.path.abspath(__file__), "run"]
    if args.mock:
        cmd.append("--mock")
        
    try:
        log_file = open(log_path, "a")
        process = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=log_file,
            stdin=subprocess.DEVNULL,
            start_new_session=True
        )
        
        with open(pid_path, "w") as f:
            f.write(str(process.pid))
            
        print(f"✅ Started background retry process with PID {process.pid}")
        print(f"📝 Logs are being written to: {log_path}")
        print("Check status with: python3 oci_helper.py status")
    except Exception as e:
        print(f"❌ Failed to start background process: {e}")
        sys.exit(1)

def run_stop(args: argparse.Namespace) -> None:
    """Gracefully terminates the background retry loop process."""
    pid_path = os.path.join(os.getcwd(), "oci_helper.pid")
    if not os.path.exists(pid_path):
        print("❌ No background retry process is running (no oci_helper.pid found).")
        sys.exit(1)
        
    try:
        with open(pid_path, "r") as f:
            pid = int(f.read().strip())
    except (ValueError, IOError) as e:
        print(f"❌ Error reading PID file: {e}")
        sys.exit(1)
        
    try:
        os.kill(pid, 0)
    except OSError:
        print(f"⚠️ Process with PID {pid} is not running. Cleaning up stale PID file.")
        try:
            os.remove(pid_path)
        except Exception:
            pass
        sys.exit(0)
        
    print(f"Stopping background process {pid}...")
    try:
        os.kill(pid, 15)  # SIGTERM
        
        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                break
        else:
            print("Process did not exit, force-killing...")
            os.kill(pid, 9)  # SIGKILL
            
        print("✅ Background retry process stopped.")
    except Exception as e:
        print(f"❌ Error stopping process: {e}")
        
    try:
        os.remove(pid_path)
    except Exception:
        pass

def run_status(args: argparse.Namespace) -> None:
    """Outputs the status of the background retry process and tails logs."""
    pid_path = os.path.join(os.getcwd(), "oci_helper.pid")
    log_path = os.path.join(os.getcwd(), "oci_helper.log")
    
    is_running = False
    pid = None
    
    if os.path.exists(pid_path):
        try:
            with open(pid_path, "r") as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            is_running = True
        except (ValueError, OSError):
            pass
            
    print("="*60)
    print("            Background Retry Process Status")
    print("="*60)
    if is_running:
        print(f"🟢 Status:    RUNNING")
        print(f"   PID:       {pid}")
    else:
        print(f"🔴 Status:    NOT RUNNING")
        if pid:
            print(f"   Note:      Stale PID file found (process {pid} died or was killed).")
            
    print(f"   Log File:  {log_path}")
    print("-"*60)
    
    if os.path.exists(log_path):
        print("Recent Log Entries (last 15 lines):")
        print("-" * 40)
        try:
            with open(log_path, "r") as f:
                lines = f.readlines()
            for line in lines[-15:]:
                print(line, end="")
        except Exception as e:
            print(f"Error reading log file: {e}")
        print("-" * 40)
    else:
        print("No log file found yet.")
    print("="*60)

# ==============================================================================
# PID File Cleanup
# ==============================================================================
def cleanup_pid() -> None:
    """Removes the tracking PID file if the current PID matches the recorded one."""
    pid_path = os.path.join(os.getcwd(), "oci_helper.pid")
    if os.path.exists(pid_path):
        try:
            with open(pid_path, "r") as f:
                pid = int(f.read().strip())
            if pid == os.getpid():
                os.remove(pid_path)
        except Exception:
            pass

# ==============================================================================
# Main Entry Point
# ==============================================================================
def main() -> None:
    """Main CLI parser and subcommand dispatcher."""
    parser = argparse.ArgumentParser(description="OCI Always Free VM Capacity Grabber")
    parser.add_argument(
        "--oci-config", 
        default="~/.oci/config", 
        help="Path to OCI API Key configuration file (default: ~/.oci/config)"
    )
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # Configure subcommand
    subparsers.add_parser("configure", help="Run interactive helper to generate config.json")
    
    # Run subcommand
    run_parser = subparsers.add_parser("run", help="Start the capacity retry loop")
    run_parser.add_argument(
        "--mock", 
        action="store_true", 
        help="Simulate a dry run of the script with mock responses (verifies notifications)"
    )
    
    # Start subcommand
    start_parser = subparsers.add_parser("start", help="Start the capacity retry loop in the background")
    start_parser.add_argument(
        "--mock", 
        action="store_true", 
        help="Simulate background run with mock responses"
    )
    
    # Stop subcommand
    subparsers.add_parser("stop", help="Stop the background capacity retry loop")
    
    # Status subcommand
    subparsers.add_parser("status", help="Check status of the background capacity retry loop")
    
    args = parser.parse_args()
    
    try:
        if args.command == "configure":
            run_configure(args)
        elif args.command == "run":
            run_loop(args)
        elif args.command == "start":
            run_start(args)
        elif args.command == "stop":
            run_stop(args)
        elif args.command == "status":
            run_status(args)
    finally:
        if args.command == "run":
            cleanup_pid()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nExecution cancelled by user. Exiting.")
        sys.exit(0)
