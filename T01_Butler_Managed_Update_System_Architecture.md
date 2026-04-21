# Butler Managed Update System Specification

This document defines the functional requirements and architectural specifications for the Butler Managed Update System. It provides sufficient detail to implement a functionally compatible version of the system using any technology stack.

## 1. System Overview
Butler is a declarative, state-based property management system for device firmware updates. It coordinates updates across a fleet of devices by managing state machines for each device/subsystem pair, ensuring that the actual state of the fleet converges to the desired state defined in a central model.

## 2. Communication Substrate Requirements
The system MUST use a message-based communication layer (e.g., MQTT) that satisfies the following:
- **Visibility:** All messages must be inspectable and loggable.
- **Format:** Messages MUST be JSON-encoded.
- **Envelope Schema:** Every message MUST contain:
    - `source`: Identity of the sender.
    - `destination`: Identity of the recipient.
    - `type`: Category of the message (e.g., `status`, `update_payload`).
    - `timestamp`: ISO-8601 formatted string.
    - `payload`: Object containing type-specific data.
- **Topic Structure:**
    - Device Status: `butler/{device_id}/status`
    - Update Command: `butler/{device_id}/update_payload`
    - Verification Results: `butler/{device_od}/verify`

All componets include a nonce in their messages to help detect situations where they are unique on the bus.

`device_id` is an alphanumeric string, e.g. `dev-001`.

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
- **Reconciliation:** Any change to a `target_version` should act as a trigger for the Orchestrator.

### 3.3 Butler Orchestrator (Control Logic)
The central engine that manages the update lifecycle state machine:
- **Quiescent State:** Device is compliant (Current == Target).
- **Active State:** Triggered when Target != Current. The Orchestrator pushes an `update_payload` containing the URL and SHA256.
- **Error State:** Triggered by device-reported failure or timeout.
- **Rollback Logic:** On critical failure, the Orchestrator MUST automatically revert the `target_version` in the Model Repository to the LKG version.
- **Trigger Detect:** Butler should automatically detect changes in the site model for the target or expected version.

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

## 5. Verification and Test Strategy

An independently executed verification agent will watch the communication channel and report if the test sequences pass or fail expecations.
This capabilty should be used by an agent developing the system to ensure that the results are correct. If the verification results
indicate test failure, they should have enough information to guide the implementing agent as to how to fix the problem.

### Verification Watcher

The Verification Watcher watches the channel and reports results if the observed sequences pass or fail expecations as accoring to the spec.
It is purely an observational test utility, used to either validate an installed system or to guide an active agent towards a complete
functioning implementation.

The verification watcher and the rest of the system can only communicate over the observable shared bus.

### Smoke Tester

The smoke tester does a simple test run with all the tools to make sure they are mostly working (no syntax errors, startup error, etc...),
but it does not verify all the functionality. Just enough of each tool to ensure the fundamentals. It's run with a simple command
that then will fork, as necessary, any sub-processes and then a basic update sequence. It checks that all the tools were able to
successfully run and send/recieve at least one message.

The smoke tester also verifies that all required arguments are enforced (i.e., will cause a usage error if omitted).

## 5. Operational Tooling Requirements
To support development, debugging, and ongoing operations, the following capabilities must be present. They
should all be simple python executables in a top-level `bin/` directory as indicated. They should all use
an implicit understanding of the underlying communication substrate. Only the indicated command line arguments
are allowed.

They are all required unless but in square brackets (e.g. [option]).

- **Setup:** A mechanism to initialize the persistent communication substrate.
  - `bin/setup`
- **Observer:** A tool to monitor and pretty-print the JSON message stream in real-time.
  - `bin/observe`
- **Register:** A tool to add a device to the model.
  - `bin/register device_id`
- **Mocket:** An implementation of a mock device that received config messages and gives mock expected results.
  - `bin/mocket device_id`
- **Butler**: The core butler program that handlers the necessary orchestration and state machine.
  - `bin/butler`
- **Trigger**: A utility that triggers necessary situations to test the system, e.g. changing the available blob version.
  - `bin/trigger device_id blob_version blob_path`
    - 'blob_version' is the semantic version of the blob (e.g. '1.3').
    - 'blob_path' is the path to the blob binary.
- **Verifier:** A monitoring utility to monitor the communication channel and report results onto the `verify` topic.
  - `bin/verifier`
- **Smoker:** A complete quick testing utility that tests all the basic components to make sure they work at basic level, but is not comprehensive.
  - `bin/smokeit`
