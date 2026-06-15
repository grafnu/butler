# Butler Managed Update System

Butler is a declarative, state-based property management system for device firmware updates. It coordinates updates across a fleet of devices by managing state machines for each device/subsystem pair.

## Prerequisites

The system requires Python 3.8+, Java, and Mosquitto.

### 1. Mandatory Peer UDMI Directory/Link Setup
The `udmi` (directory or link) MUST exist as a peer directly sibling to the repository directory (e.g., at `../udmi` relative to the repository root directory). If this sibling directory or symlink is missing, it is an unrecoverable error.
- **Shared Resource Constraint:** The peer `../udmi` directory is a **shared, read-only resource** that is only suitable for running standard immutable executables or referencing specifications. Do NOT modify any files directly inside `../udmi/` (such as running setup tasks or cloning models there) to avoid execution conflicts in multi-client/multi-implementation environments.
- **Expected Layout:** The peer `../udmi/` directory must contain standard UDMI CLI utilities inside `bin/` (such as `setup_base`, `start_local`, `clone_model`, `start_dut`, `site_trigger`) and the formal UUFI specification file at `docs/specs/uufi.md`.
- **Relative Path Resolution:** To ensure interoperability across multiple directories, all components (including the Device Under Test) MUST resolve relative `file://` paths specified in the Software Catalog (`model.json`) relative to the workspace/project root directory (not relative to the peer `../udmi` or local execution directory).
- **Keep Up to Date:** If the `../udmi/` peer subdirectory is configured as a git repository, ensure it matches the current active branch and keep it up to date (e.g., by executing `git pull` in that directory).

### 2. Install System Dependencies
To simplify system bootstrapping, you can delegate the installation of all system-level dependencies (such as Mosquitto, mosquitto-clients, expect, and development packages) to the UDMI setup utility:

```bash
../udmi/bin/setup_base
```
*Note on Privileges:* Running `../udmi/bin/setup_base` is **optional** if you already have the required dependencies (Python 3.8+, Java 11+, Mosquitto broker, mosquitto-clients, and expect) pre-installed on your system.
*(On macOS, please install Mosquitto and Java via Homebrew manually: `brew install mosquitto openjdk`)*

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

<!-- ASSUMPTION: On a clean checkout of the main branch, butler/ and bin/ are generated spec-driven directories and are not checked in by default. -->
> **Note on Workspace Layout**: On the `main` branch, the `butler/` (core Python logic) and `bin/` (operational executables) folders are not checked in because they are built from specifications as part of a spec-driven agent workflow (see `REBUILD.md`). On implementation branches (such as `impl_B`), these directories are pre-populated.
> If you are starting on `main` and these directories are missing, you must first generate or check out the implementation before installing Python requirements or running executables.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r butler/requirements.txt
```

### 2. Connectivity Specifications & BUTLER_CONN_SPEC
The communication bus specification complies with the `uufi.md` specification (`udmi/docs/specs/uufi.md`).
- **Default/Fallback Spec**: When not specified, the system defaults to `mqtt://<branchname>@localhost/` (or `mqtt://unknown@localhost/` if not in a git workspace).
- **Environment Variable**: If the `BUTLER_CONN_SPEC` environment variable is defined in the shell, you must explicitly pass that specification value as the connection argument to all tools:
  ```bash
  # Example if BUTLER_CONN_SPEC is set
  bin/setup $BUTLER_CONN_SPEC
  ```

### 3. Initialize the Local Workspace and Broker Setup
To prevent execution conflicts when running multiple disparate implementations side-by-side using the same UDMI install directory, always run from your respective local working directory and use unique ports and cloned site models.

First, copy the pre-existing test site model from the shared peer `../udmi` directory into a local `testing/udmi_site_model` directory. This creates an isolated local workspace site model that we can safely modify during testing without affecting other parallel trials or the shared resource:
```bash
mkdir -p testing
cp -r ../udmi/sites/udmi_site_model testing/udmi_site_model
```

Next, define your chosen unique port (e.g., `40050`) as a shell variable, run the setup script to prepare the communication bus, and perform a connectivity check. If the local MQTT broker is not already running on that port, the setup script will automatically invoke the peer UDMI tool (specifically `../udmi/bin/start_local`) to start it on that unique port:
```bash
# Define your unique port
mqtt_port=40050

# Run the setup script using the port variable
bin/setup mqtt://localhost:$mqtt_port/
```
**Expected behavior:** The setup utility verifies that the sibling/peer `../udmi` directory or link exists (raising a hard fail on startup if it is missing). It then checks port `$mqtt_port` connectivity and automatically invokes the UDMI local setup utility (`../udmi/bin/start_local`) to start and configure the local MQTT broker in non-sudo mode on your unique port.

## Manual Operation

To run the components individually and observe their behavior:

*(Ensure that the `mqtt_port` variable is defined or exported in your terminal windows, e.g., `mqtt_port=40050`)*

### 1. Start the Orchestrator
```bash
bin/butler mqtt://localhost:$mqtt_port/
```
**Expected behavior:** The orchestrator starts, outputs its connectivity parameters to stderr, and reactively waits for model updates and state messages over the UUFI bus on the unique port to coordinate updates.

### 2. Start the Verifier (Optional)
The verifier monitors the bus and validates device state transitions.
```bash
bin/verifier mqtt://localhost:$mqtt_port/
```
**Expected behavior:** The verifier starts, outputs its connectivity parameters, and listens to State and Config messages, logging compliant state transitions and any validation errors.

### 3. Start the Device Under Test (Pubber DUT)
Using the standard UDMI/UUFI client located in the peer `../udmi/` directory (executed from your working directory and pointing to your local site model copy):
```bash
../udmi/bin/start_dut testing/udmi_site_model mqtt://localhost:$mqtt_port/ AHU-1 "uufi-serial"
```
**Expected behavior:** The simulated device starts up, connects to the local broker on the unique port, and begins publishing periodic state reports including its current running software version.

### 4. Trigger a Managed Update
Initiate a managed software update by updating the expected version configuration in the local site model and publishing a model update event over the UUFI bus:
```bash
../udmi/bin/site_trigger update testing/udmi_site_model AHU-1 system 1.1.0
```
**Expected behavior:**
- The `site_trigger` utility (located in the peer `../udmi/bin/`) updates the local physical site model on disk and publishes a `model/cloud` update event.
- The **Butler** detects the version drift, queries the Software Catalog (`udmi_blob_store/model.json`) for package metadata, and publishes a `blobset` config payload instructing the device to upgrade.
- The **Device** (DUT) transitions to the `pending` state to apply the update.
- Upon completion, the device reports `success` and its new actual version `1.1.0` in its state messages, transitioning the system to the `quiescent` state.
- The **Verifier** logs state transitions and validation success.

## Testing

### Smoke Test
Run a quick end-to-end smoke test that verifies basic component connectivity on the unique port:
```bash
bin/smokeit mqtt://localhost:$mqtt_port/
```
**Expected behavior:** The script will launch temporary instances of the system components, run a sample update, and output "Smoke test passed" (or a detailed error if something is misconfigured). To ensure reliable execution against standard Pubber devices, the `smokeit` utility will log any Pubber-specific state transition limitations as soft warnings rather than hard failures.

## Documentation
For detailed specifications and component requirements, see:
- `spec/butler.md`: Main Butler orchestrator specification.
- `spec/blobstore.md`: BlobStore provider interface and implementations.
- `spec/update.md`: Software update message sequence diagram.
- `AGENTS.md`: Mandates and instructions for agentic systems.
