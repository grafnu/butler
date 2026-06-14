# Butler Managed Update System

Butler is a declarative, state-based property management system for device firmware updates. It coordinates updates across a fleet of devices by managing state machines for each device/subsystem pair.

## Prerequisites

- **Python 3.8+**
- **Mosquitto** (MQTT Broker)

### Install Mosquitto
On Ubuntu/Debian:
```bash
sudo apt-get update
sudo apt-get install mosquitto mosquitto-clients
```

On macOS (using Homebrew):
```bash
brew install mosquitto
brew services start mosquitto
```

## Project Structure

The Butler system is organized as follows:

- **spec/**: Formal architectural and protocol specifications (e.g., `butler.md`, `blobstore.md`, `update.md`).
- **bin/**: Operational executables and tooling for the system.
- **butler/**: Core implementation logic (Python).
- **README.md**: This overview document.
- **AGENTS.md**: Mandatory instructions and constraints for agentic systems.
- **REBUILD.md/UPDATE.md/AUDIT.md/MERGER.md/WORKFLOW.md**: System procedures and workflows.
- **.wincolor/.gitignore**: Environment and git configuration.
- **impl/**: Cross-implementation testing workspace.
- **testing/**: Test assets and simulation environments.
- **udmi_blob_store/**: Static testing Software Catalog and blobs (parallels `udmi_site_model`).
- **tmp/**: Ephemeral workspace for temporary files.
- **venv/**: Python virtual environment.

## Local Development Setup

### 1. Initialize Virtual Environment
If not already present, create and activate a Python virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r butler/requirements.txt
```

### 2. Start MQTT Broker
Ensure `mosquitto` is running in the background. If it's not running as a service:
```bash
mosquitto
```

### 3. Initialize the System
Run the setup script to prepare the communication bus and perform a connectivity check. If the local MQTT broker is not already running, the setup script will automatically invoke the local UDMI tool (specifically `udmi/bin/start_local`) to start it:
```bash
bin/setup mqtt://localhost
```
**Expected behavior:** The script verifies that the local `udmi/` subdirectory exists (raising a hard fail on startup if it is missing), checks connection status, starts the broker using `udmi/bin/start_local` if necessary, and outputs "Bus setup complete."

## Manual Operation

To run the components individually and observe their behavior:

### 1. Start the Orchestrator
```bash
bin/butler mqtt://localhost
```
**Expected behavior:** The orchestrator starts, outputs its connectivity parameters to stderr, and reactively waits for model updates and state messages over the UUFI bus to coordinate updates.

### 2. Start the Verifier (Optional)
The verifier monitors the bus and validates device state transitions.
```bash
bin/verifier mqtt://localhost
```
**Expected behavior:** The verifier starts, outputs its connectivity parameters, and listens to State and Config messages, logging compliant state transitions and any validation errors.

### 3. Start the Device Under Test (Pubber DUT)
Using the standard UDMI/UUFI client located in the local `udmi/` subdirectory:
```bash
./udmi/bin/start_dut ./udmi/sites/udmi_site_model mqtt://localhost/ AHU-1 "uufi-serial"
```
**Expected behavior:** The simulated device starts up, connects to the local broker, and begins publishing periodic state reports including its current running software version.

### 4. Trigger a Managed Update
Initiate a managed software update by updating the expected version configuration in the site model and publishing a model update event over the UUFI bus:
```bash
./udmi/bin/site_trigger update ./udmi/sites/udmi_site_model AHU-1 system 1.1.0
```
**Expected behavior:**
- The `site_trigger` utility updates the physical site model on disk and publishes a `model/cloud` update event.
- The **Butler** detects the version drift, queries the Software Catalog (`udmi_blob_store/model.json`) for package metadata, and publishes a `blobset` config payload instructing the device to upgrade.
- The **Device** (DUT) transitions to the `pending` state to apply the update.
- Upon completion, the device reports `success` and its new actual version `1.1.0` in its state messages, transitioning the system to the `quiescent` state.
- The **Verifier** logs state transitions and validation success.

## Testing

### Smoke Test
Run a quick end-to-end smoke test that verifies basic component connectivity:
```bash
bin/smokeit mqtt://localhost
```
**Expected behavior:** The script will launch temporary instances of the system components, run a sample update, and output "Smoke test passed" (or a detailed error if something is misconfigured).

## Documentation
For detailed specifications and component requirements, see:
- `spec/butler.md`: Main Butler orchestrator specification.
- `spec/blobstore.md`: BlobStore provider interface and implementations.
- `spec/update.md`: Software update message sequence diagram.
- `AGENTS.md`: Mandates and instructions for agentic systems.
