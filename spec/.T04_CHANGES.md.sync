# Changes for Butler Managed Update System Architecture
Updated: 2026-04-29 10:01:24

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
### REPLACE in tab 'Butler Managed Update System Architecture' at the beginning of the tab
**OLD CONTENT:**
# Butler Managed Update Vibrant
## Butler Update Management Overview
Butler is a system that exists in a world of fleet management where a centralized controller is used to manage a fleet of devices. It uses an already established communication mechanism with the target devices to control sequenced operations in a regular and efficient manner. Internally, it manages state machines and other control logic to coordinate. The explicit goal is to have a declarative state-based property management system for devices.
### Ecosystem Architecture
The butler ecosystem consists of a number of components that work together to provide the complete offering. All components communicate in such a way that they satisfy the constraints of the Communication Substrate component. Device update management is sharded out into "software subsystems" where a particular subsystem for a device can be mapped to a specific blob version of that software.
#### Control Mechanism
This is the core mechanism for managing a state machine for each device and each software bundle, and each tuple of { device, bundle } has an independently managed state machine. Server state transitions are determined by input from the device state or the model repository.
* quiescent
  * model update triggers to active state
* active
  * delivery: Butler resolves the GCS path, fetches the SHA256 hash, and pushes a secure, time-limited Signed URL payload to the device via the Barbican routing layer.
  * success: The device completes the update, publishes its new software identity, and Butler updates the Site Model to reflect compliance.
* error
  * device_error: The device aborts the installation due to failed hash verification or a fatal system error, publishing a 500-level ERROR state.
  * rollback: Butler detects the fatal device error and autonomously triggers a cloud-side rollback by reverting the Target Configuration in the Site Model to the last known-good version.
#### Device Conduit
The Device Conduit represents the crucial communications layer responsible for all interaction between the server-side Control Mechanism and the device fleet. 
##### State Machine
The client states listed below reflect the device's status as it moves through the update lifecycle, from stable operation to actively processing a change.
* Client states
  * quiescent
  * pending
  * success
  * failure
##### UDMI Binding
See uufi.md as the specification for interfacing with UDMI.
#### Monitoring Dashboard
The Monitoring Dashboard serves as the integrated diagnostic interface for viewing the system's current state and internal control logic. While the Butler Orchestration Engine performs the continuous monitoring and alerting on errors, this dashboard is the user-facing capability for operators to query and inspect the process. Its primary function is to provide transparency into the update lifecycle, which is essential for diagnosing issues, confirming compliance, and managing scaled rollouts.
Key monitoring capabilities and state visibility include:
* Displaying the state machine status for each unique tuple of { device, bundle }, including quiescent, active, and error states.
* Providing visibility into complex device transitions, such as active *delivery* status, *success* confirmation, and cloud-side *rollback* actions triggered by *device_error* states.
* Visualizing the progress and compliance of updates across the fleet, supporting the management of tunable rollouts (e.g., graded upgrade paths).
* Showing relevant internal states and error alerts to aid in debugging and ensuring the robustness of the update process.
#### Blob Repository
The Blob Repository is a new internal component implemented as a secure Google Cloud Storage (GCS) object store for managing and hosting firmware versions, referred to as 'blobs'. It enforces a strict, deterministic pathing structure for file storage (e.g., *gs://{bucket}/{make}/{model}/{type}/{version}/bundle.bin*). Files are ingested with mandatory custom metadata that includes the exact 64-character SHA256 hash, which is critical for the device's cryptographic integrity verification before execution.
#### Model Repository
The model repository holds the expected state of any target device. This provides a lookup mechanism to determine the particular version of a blob that any device should be using for a specific software subsystem. Automatic change detection on the model acts as a trigger for device updates. When the expected bundle setting for a device is out of sync with the current bundle version an update is triggered.
##### UDMIS Binding
UDMIS service can provide the necessary information by providing an API for model query/response and change notification.
#### Communication Substrate
The communication substrate connects all of the other components together. The core Control Mechanism does all its communication to other components through a communication channel that satisfies the core requirements of the substrate:
* All communication messages must be externally visible so they can be logged and inspected.
* All messages are inspectable in a predictable JSON format that is defined by an explicit schema.
* Message attributes clearly identify the source, destination, and type of the message.
* An authenticated utility program is available to tap into any message stream to log and inspect the message stream.
**NEW CONTENT:**
# Butler Managed Update System Specification
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
### INSERT in tab 'Butler Managed Update System Architecture' right after 'An authenticated utility program is available to tap into any message stream to log and inspect the message stream.'
**NEW CONTENT:**
This document defines the functional requirements and architectural specifications for the Butler Managed Update System. It provides sufficient detail to implement a functionally compatible version of the system using any technology stack.

## 1. System Overview
Butler is a declarative, state-based property management system for device firmware updates. It coordinates updates across a fleet of devices by managing state machines for each device/subsystem pair, ensuring that the actual state of the fleet converges to the desired state defined in a central model.

### File Structure

There should be no files or directories at the top-level other than those explicitly listed here:

* AGENTS.md: Generic instructions for agentic systems.
* BUTLER.md: Specific instructions on how things for this repo.
* README.md: Human-centric documentation.
* `spec/`: Input specifications.
* `bin/`: Operational tooling programs.
* `butler/`: Core python files generated by a build.
  * `requirements.txt`: Python package requirements for butler.
* `tmp/`: Any temporary files. Can be deleted anytime without loss of anything signficant.
* `testing/`: Files created specifically for running tests (smoke or otherwise)
  * `blobs/`: Blobs used for testing updates.
* `venv/`: Python virtual environment.

## 2. Communication Substrate Requirements
The system MUST use a message-based communication layer (e.g., MQTT) that satisfies the following:
- **Visibility:** All messages must be inspectable and loggable.
- **Format:** Messages MUST be JSON-encoded.

### UDMI Binding

See `uufi.md` for the binding of the communication substrate to UDMI.

### MQTT Binding

The MQTT binding should follow the UDMI message format, but encode the necessary message attributes in a canonical MQTT topic.

## 3. Functional Components

### 3.1 Blob Repository (Storage)
Responsible for immutable firmware version management.
- **Path Convention:** Must follow a deterministic structure: `{make}/{model}/{subsystem}/{version}/`.
- **Integrity:** Every blob MUST be associated with a SHA256 hash.
- **Access:** Must provide a mechanism (e.g., Signed URLs or local file pointers) for devices to securely retrieve blobs.

### 3.2 Model Repository (Desired State)
Responsible for tracking the "source of truth" for the fleet.
- **State Tracking:** Must maintain `target_version` and `current_version` for every device subsystem.
- **History:** Must track the `last_known_good` (LKG) version for each subsystem to support recovery.
- **Reconciliation:** Any change to a `target_version` should act as a trigger for the Butler Orchestrator.
- **Environment Isolation:** The repository MUST support an environment variable `BUTLER_MODEL_FILE` to override the default model storage path. This is critical for parallel testing and isolating demo environments from developer state.
- **Atomicity:** All updates to the model file MUST be atomic (e.g., write to a temporary file and rename) to prevent corruption during system crashes.

### 3.3 Butler Orchestrator (Control Logic)
The central engine that manages the update lifecycle state machine:
- **Quiescent State:** Device is compliant (Current == Target).
- **Active State:** Triggered when Target != Current. The Orchestrator pushes an `update_payload` containing the URL and SHA256.
- **Error State:** Triggered by device-reported failure or timeout.
- **Rollback Logic:** On critical failure, the Orchestrator MUST automatically revert the `target_version` in the Model Repository to the LKG version.
- **Trigger Detect:** Butler should automatically detect changes in the site model for the target or expected version.
- **Timeout Management:** The Orchestrator MUST implement a configurable timeout (default 60s) for devices in the `pending` state. If a device fails to report `success` or `failure` within this window, it must be treated as a failure and potentially trigger a rollback.

### 3.4 Device Conduit (Client-side)
The implementation on the device must adhere to this state flow:
1. **Report Status:** Periodically publish current version and state (`quiescent`).
2. **Handle Update:** Upon receiving `update_payload`, transition to `pending`.
3. **Verify & Apply:** Download the blob, verify the SHA256 hash, and apply the update.
4. **Finalize:** Report `success` (if verified) or `failure` (if hash mismatch or install error).

## 4. System Behaviors

### 4.1 Update Sequence
1. Model Repository updates `target_version`.
2. Orchestrator detects mismatch and fetches metadata from Blob Repository.
3. Orchestrator publishes `update_payload` to the device.
4. Device reports `pending`, applies update, then reports `success`.
5. Orchestrator updates Model Repository to reflect the new `current_version`.

### 4.2 Rollback Sequence
1. Device receives `update_payload` but fails verification.
2. Device publishes `status` with state `failure`.
3. Orchestrator identifies the failure and lookups the `last_known_good` version.
4. Orchestrator updates the `target_version` back to the LKG.
5. A new Update Sequence begins for the LKG version.

### 4.3 Idempotency and Robustness
- **Duplicate Message Handling:** All components MUST be idempotent. Receiving the same `update_payload` or `status` message multiple times must not lead to inconsistent state transitions.
- **Message Nonce:** The `nonce` (8-digit hex) MUST be used to uniquely identify messages. Components SHOULD track recently seen nonces to ignore duplicates if the underlying transport (e.g., MQTT QoS 1) delivers them multiple times.
- **Graceful Restart:** Components MUST be able to recover their internal state by observing the latest `status` messages on the bus or querying the Model Repository upon startup.

## 5. Verification and Test Strategy

An independently executed verification agent will watch the communication channel and report if the test sequences pass or fail expecations.
This capabilty should be used by an agent developing the system to ensure that the results are correct. If the verification results
indicate test failure, they should have enough information to guide the implementing agent as to how to fix the problem.

### Verification Watcher

The Verification Watcher watches the channel and reports results if the observed sequences pass or fail expecations as accoring to the spec.
It is purely an observational test utility, used to either validate an installed system or to guide an active agent towards a complete
functioning implementation. When it detects a valid sequence, or an invalid message, it will output a validation message.

The verification watcher and the rest of the system can only communicate over the observable shared bus.

### Test mode

Add an `-f` flag to both `mocket` and `butler` that introduces a failure mode of some kind. E.g., it does not progress to the next intended
state. When this is the case, `verifier` should detect that there was an invalid state transition and indicate that the sequence failed.

### Smoke Tester

The smoke tester does a simple test run with all the tools to make sure they are mostly working (no syntax errors, startup error, etc...),
but it does not verify all the functionality. Just enough of each tool to ensure the fundamentals. It's run with a simple command
that then will fork, as necessary, any sub-processes and then a basic update sequence. It checks that all the tools were able to
successfully run and send/recieve at least one message.

The smoke tester also verifies that all required arguments are enforced (i.e., will cause a usage error if omitted).

All temporary working files from the smoke test should be sequestered in a `testing` subdirectory so they don't pollute the
overall directory.

## 6. Operational Tooling Requirements
To support development, debugging, and ongoing operations, the following capabilities must be present. They
should all be simple python executables in a top-level `bin/` directory as indicated. They should all use
an implicit understanding of the underlying communication substrate. Only the indicated command line arguments
are allowed. There should be no other files in the `bin/` directory that are not explicitly listed here.

All commands should be restartable without any problems if they are in a quiescent state. If the are restarted
in the middle of a transation it's OK if the system reports a transient error as long as it recovers to a stable
state. Retrying the transation should then behave as expected (assuming no other restarts).

They are all required unless but in square brackets (e.g. [option]).

- **Setup:** A mechanism to initialize the persistent communication substrate.
- `bin/setup`
- **Observer:** A tool to monitor and pretty-print the JSON message stream in real-time.
- `bin/observe`
- **Register:** A tool to add a device to the model.
- `bin/register device_id`
- **Mocket:** An implementation of a mock device that received config messages and gives mock expected results.
- `bin/mocket device_id`
- Tag should be `mockit` in messages source and logging
- **Butler**: The core butler program that handlers the necessary orchestration and state machine.
- `bin/butler`
- Tag should be `butler` in messages source and logging
- **Trigger**: A utility that triggers necessary situations to test the system, e.g. changing the available blob version.
- `bin/trigger device_id blob_version blob_path`
- 'blob_version' is the semantic version of the blob (e.g. '1.3').
- 'blob_path' is the path to the blob binary.
- **Verifier:** A monitoring utility to monitor the communication channel and report results onto the `verify` topic.
- `bin/verifier`
- Tag should be `verifier` in messages source and logging
- **Smoker:** A complete quick testing utility that tests all the basic components to make sure they work at basic level, but is not comprehensive.
- `bin/smokeit`
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
