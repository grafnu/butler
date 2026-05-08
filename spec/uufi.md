[**UDMI**](../../) / [**Docs**](../) / [**Specs**](./) / [UUFI](#)

# Unified UDMI Functional Interface (UUFI)

The **Unified UDMI Functional Interface (UUFI)** is a specification for external applications to integrate with a UDMI-managed system. It formalizes the communication channel between an external application (the **Client**) and the UDMI cloud infrastructure (the **System**) using a standardized messaging mechanism.

UUFI provides a "clean room" interface for programmatic control of UDMI operations, including device management, telemetry consumption, and command injection, all while adhering to the standard UDMI schemas. It supports both **GCP PubSub** and **MQTT** as transport layers.

## 1. Architecture Overview

UUFI utilizes a messaging transport where the Client interacts with the System via dedicated topics and subscriptions. This connection acts as a gateway for all UDMI messages.

*   **Managed Registry:** The actual IoT registry containing physical or virtual devices being managed.
*   **System Interface:** The set of topics provided by the UDMI infrastructure to handle UUFI traffic.

### Message Flow
- **Publish (into UDMI):** The Client publishes a UDMI message to the **UUFI** topic. The message is wrapped in a UUFI Envelope.
- **Receive (from UDMI):** The System delivers messages from managed devices to the Client via a **UUFI** reply channel. Messages are encapsulated in a UUFI Envelope.

## 2. Connectivity and Authentication

### 2.1. Connection String Designator
To provide a standard way to connect into the system using a single string designator, UUFI interfaces support a URL-like connection string format. The two supported schemes are `mqtt://` and `pubsub://`.
* `mqtt` uses the industry standard [mqtt protocol](https://github.com/mqtt)
* `pubsub` uses [Google Cloud Platform's PubSub](https://cloud.google.com/pubsub)

You can use the optional `user@` and `:port` as necessary within the URL format.
* If `user@` is not specified then it should default to `unknown`
* The `@` character is only allowed if it is preceded by a non-empty `user` identifier.
* If `:port` is not specified, then it should default to the protocol-specific meaningful default.

`PubSub` is considered a "singular" receiver binding in that if multiple entities want to use the same
channel, they must use different `user` identities, otherwise they will
not all receive every message. 

#### Protocol Mapping

**PubSub (`pubsub://`)**
*   The base `host` maps to the GCP project.
*   The `principal` is derived from the (optional) `user@` component into `user@`
    * If `user` is not defined then it defaults to `unknown`
    *   The entire string in the `user` component is used as the identity.

    * The principal is included in the message envelope when publishing a message to the topic.
      * This includes the `@`
*   The `user` component maps to a suffix on the subscription.
*   The first URL path part, if present, maps to the root name to use instead of `udmi_uufi`.
*   **Note:** The `:port` component is NOT allowed for `pubsub://` URLs.
*   *Example:* `pubsub://the-user@my-project/a-topic` maps to:
  * The GCP project `my-project`
  * The topic `a-topic`
  * The principal `the-user@`
  * The receive subscription `a-topic+the-user`
  * Messages have an attribute `principal` that is `the-user@`
*   *Example:* `pubsub://user2.10@diff-project` maps to:
  * The GCP project `diff-project`
  * The topic `udmi_uufi`
  * The principal `user2.10@`
  * The receive subscription `udmi_uufi+user2.10`
  * Messages have an attribute `principal` that is `user2.10@`

Not all received messages will have a `principal` attribute as some are generic (e.g. telemetry received from a building). Only
messages that are explicitly intended for the recipient (e.g. message acks) will have this attribute present. The subscription
should be filtered to only include messages that have this (matching) attribute or the attribute missing. Received UUFI
will have a `principal` indicating the **Session Owner** (the identity of the entity managing that specific communication channel), rather than strictly the sender or receiver. All messages in/out from one entity will have the same `principal` attribute value.

**MQTT (`mqtt://`)**
*   The base `host` and `:port` map as expected (network address).
*   **Topic Isolation (Pattern C):** All MQTT topic paths MUST be prefixed with the principal identifier to ensure session isolation. The principal is derived from the `user@` component of the connection string (defaulting to `unknown`).
  *   **Structure:** `/uufi/p/{principal}/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}`
  *   *Example:* `mqtt://butler@localhost` uses prefix `/uufi/p/butler/`.
*   **Cloud Model Service:** The Cloud Model is managed as an MQTT-based service.
  *   **Discovery:** Clients (like the Butler) MUST publish a `query/cloud` message to the registry-less topic `/uufi/p/{principal}/c/query/cloud`.
  *   **Responder Role:** A Model-Hosting component (System/Mocket) MUST subscribe to these queries and respond by publishing the current model to the `/uufi/p/{principal}/c/config/cloud` topic.
  *   **Model Schema:** The Cloud Model MUST use the nested **Registries** structure to support multi-registry environments (see Section 5.1).

### 2.2. PubSub Transport
The Client must have access to the GCP project where the UDMI system is deployed.

*   **Project ID:** The GCP project ID.
*   **Publish Topic:** `udmi_uufi` (or a namespace-prefixed version like `prefix-udmi_uufi`).
*   **Receive Subscription:** A subscription to the `udmi_uufi` topic (e.g., `prefix-udmi_uufi-user_id`).
*   **Authentication:** Standard **GCP IAM**.

### 2.3. MQTT Transport (Local Mosquitto)
For local testing or on-premise deployments, a standard MQTT broker (like Mosquitto) can be used.

*   **Broker URL:** Typically `tcp://localhost:1883` or `ssl://localhost:8883`.
*   **Topic Structure:** `/uufi/p/{principal}/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}`
*   **Authentication:** Username/Password or mTLS (certificate-based).

## 3. Handshake Protocol

Upon connection, the Client must perform a handshake to synchronize with the System. **The Handshake is always initiated by the Client. The System MUST NOT initiate a handshake unless it is acting as a Client to a higher-level System.**

1.  **State Declaration:**
 The Client publishes a UDMI `state` message to the UUFI topic. This message must include a `udmi` subfolder with a `setup` block (see `state_udmi.json`).
    -   `functions_ver`: The version of the UDMI functions the Client expects.
    -   `transaction_id`: A unique ID for the handshake transaction.
    -   **Addressing:** The Client MUST use the registry-less `/uufi/p/{principal}/c/state/udmi` topic and include its unique identity in the `source` field in the envelope.

2.  **Configuration Confirmation:** The System responds via the reply channel by updating the Client's `config`. This message includes a `udmi` subfolder (see `config_udmi.json`) containing:
    -   `setup`: System version information (min/max supported function versions).
    -   `reply`: A copy of the Client's setup block to confirm receipt.
    -   **Addressing:** The System MUST publish the reply to the `/uufi/p/{principal}/c/config/udmi` topic. Clients filter incoming messages by `transactionId` or `principal` in the envelope.

The Client is considered **Active** only after receiving a configuration reply where the `transaction_id` inside the `udmi.reply` block matches the `transaction_id` sent in the original `state` message.

**Transaction Integrity:** Implementations MUST ignore Handshake configuration replies if the `reply.transaction_id` does not match the currently active `handshake_tid`. Receipt of an unmatched transaction ID MUST NOT activate the client. This prevents a restarting component from accidentally "activating" on a leftover message from a previous session.

### Handshake Addressing
Because the initial handshake is generic and occurs before the Client is associated with a specific registry or device, the registry-less Pattern C structure is used:

- **PubSub:** The `deviceRegistryId` and `deviceId` message attributes MUST be not present empty strings (or `null`).
- **MQTT:** The topic MUST be `/uufi/p/{principal}/c/{subType}/{subFolder}`.
    - **Principal Fallback:** If a `{principal}` is not explicitly provided in the connection configuration, the Client MUST generate a unique identity (e.g., using its process name and a UUID or timestamp) to use in the topic structure.

**Important:** Handshake messages MUST be addressed using this registry-less scheme instead of registry-based addressing (`/uufi/r/.../c/...`).

### 3.1. Registry ID Discovery
Registry-based addressing (`/uufi/r/{registryId}/.../c/...`) requires knowledge of the `registryId`. 
- **Mandatory Default:** In the absence of a specific configuration, implementations MUST default to `default` as the `{registryId}`.
- **System Configuration:** The System MAY inform the Client of the appropriate `{registryId}` via the `config.udmi` block during the handshake.

### Timeouts and Retries
The handshake MUST be completed within 60 seconds. If no matching configuration reply is received within this window, the Client MUST log a critical error and terminate the connection (Fail-fast). Retries with exponential backoff SHOULD only be used if the connection itself fails before the handshake can be initiated.

## 4. Message Encapsulation

All messages exchanged via UUFI are wrapped in a **UUFI Envelope**.

### Mandatory Payload Fields
To ensure compatibility with UDMI standards and verification tools, the following fields MUST be included in the inner JSON `payload` object for all messages:
- `timestamp`: RFC 3339 timestamp of when the message was generated.
- `version`: The UDMI schema version (e.g., `1.5.2`).

### Envelope Fields
The following fields are available in the envelope to provide context for the message. Their presence depends on the transport and specific operation (they are not globally mandatory):
- `projectId`: The project identifier.
- `deviceRegistryId`: The `registry_id` of the Managed Registry.
- `deviceId`: The target or source device ID in the Managed Registry (e.g., `BLD-1`, `_validator`).
- `subFolder`: The UDMI subfolder (e.g., `pointset`, `system`, `validation`).
- `subType`: The UDMI message type (e.g., `events`, `state`, `config`, `commands`).
- `transactionId`: A unique string used to track requests and responses.
- `publishTime`: RFC 3339 timestamp of when the message was wrapped.
- `source`: An identifier for the Client's session/context (distinct from the identity used in the UDMI payload).

### Transport Mapping

| Transport | Envelope Location | Payload Location |
| :--- | :--- | :--- |
| **PubSub** | Message Attributes | Message Data (JSON) |
| **MQTT** | Topic Structure & Payload | Payload `payload` field |

#### MQTT Topic Structure
MQTT topic paths follow a unified structure where registry and device segments are optional, but the channel segment `c/` is mandatory:
- **Structure:** `/uufi/[r/{registryId}/[d/{deviceId}/]]c/{subType}/{subFolder}`
- **Constraint:** A device segment `d/` MUST NOT be present if the registry segment `r/` is absent.

#### MQTT Message Wrap
Since MQTT 3.1.1 does not support separate attributes, the envelope fields are included in the JSON payload alongside the actual UDMI message. **Crucially, the top-level JSON envelope fields MUST only include data NOT already encoded in the MQTT topic structure (e.g., omitting projectId, deviceId, etc.).**

```json
{
  "transactionId": "UUFI:sess123:002",
  "payload": {
    "version": "1.5.2",
    "timestamp": "2026-04-29T10:05:00Z",
    "pointset": {
      "room_temperature": { "set_value": 22.5 }
    }
  }
}
```

## 5. Operational Commands

UUFI supports direct operations on the Cloud Model by setting specific attributes.

### 5.1. Cloud Model Schema
The `CloudModel` object used in these operations contains:
- `operation`: The action to perform (`READ`, `CREATE`, `UPDATE`, `DELETE`, `BIND`, `UNBIND`).
- `registries`: A map where keys are `registry_id`, values are maps of `device_id` to subsystem states.
  - *Example structure:* `{"registries": {"reg-A": {"devices": {"dev-001": {"main": {"target_version": "1.1.0", "current_version": "1.0.0"}}}}}}`
- `detail`: (Optional) Additional parameters specific to the operation.

### 5.2. Cloud Model Queries
- Set `subFolder: cloud` and `subType: query`.
- **Payload:** A `CloudModel` object with `operation: READ`.

### 5.3. Cloud Model Updates
- Set `subFolder: cloud` and `subType: model`.
- **Payload:** A `CloudModel` object specifying the `operation` (e.g., `CREATE`, `UPDATE`, `DELETE`, `BIND`, `UNBIND`) and the target `devices` map.

## 6. Mapping UDMI to UUFI Envelopes

| UDMI Operation | Envelope `subType` | Envelope `subFolder` | Direction |
| :--- | :--- | :--- | :--- |
| **Handshake State** | `state` | `udmi` | Publish |
| **Handshake Config** | `config` | `udmi` | Receive |
| **Device Config Update** | `config` | *varies* (e.g., `pointset`) | Publish |
| **Device State Event** | `state` | *varies* (e.g., `system`) | Receive |
| **Device Telemetry** | `events` | `pointset` | Receive |
| **Device Discovery** | `events` | `discovery` | Receive |
| **Model Query** | `query` | `cloud` | Publish |
| **Model Update** | `model` | `cloud` | Publish |
| **Model Reply** | `config` | `cloud` | Receive |
| **Update Config** | `config` | `update` | Publish |
| **Update State** | `state` | `update` | Receive |
| **Error Reporting** | `errors` | *varies* (e.g., `pointset`) | Receive |

**Note on Managed Updates:** For firmware and software lifecycle management, the `update` subfolder MUST be used for both `config` (triggers) and `state` (reporting). The `system` subfolder is reserved for general device health and metadata.

## 7. Examples

The following examples demonstrate how to format PubSub messages for common UUFI operations, grouped by logical exchange.

### 7.1. Handshake Exchange
The handshake synchronizes the Client and the System upon connection.

#### Step 1: Publish Handshake State
The Client initiates the session using generic addressing.

**PubSub Attributes:**
```json
{
  "projectId": "my-gcp-project",
  "deviceRegistryId": "",
  "deviceId": "",
  "subFolder": "udmi",
  "subType": "state",
  "transactionId": "UUFI:sess123:001",
  "nonce": "a1b2c3d4",
  "source": "my-user-id",
  "principal": "my-user-id@"
}
```

**PubSub Data (JSON):**
```json
{
  "version": "1.5.2",
  "timestamp": "2026-04-29T10:00:00Z",
  "udmi": {
    "setup": {
      "functions_ver": 9,
      "transaction_id": "UUFI:sess123:001",
      "msg_source": "my-user-id",
      "user": "my-user-id"
    }
  }
}
```

#### Step 2: Receive Handshake Config
The System confirms the session is active.

**PubSub Attributes:**
```json
{
  "projectId": "my-gcp-project",
  "deviceRegistryId": "",
  "deviceId": "",
  "subFolder": "udmi",
  "subType": "config",
  "transactionId": "UUFI:sess123:001",
  "nonce": "a1b2c3d4",
  "principal": "my-user-id@"
}
```

**PubSub Data (JSON):**
```json
{
  "version": "1.5.2",
  "timestamp": "2026-04-29T10:00:01Z",
  "udmi": {
    "setup": {
      "functions_min": 9,
      "functions_max": 9,
      "udmi_version": "1.5.2"
    },
    "reply": {
      "functions_ver": 9,
      "transaction_id": "UUFI:sess123:001",
      "msg_source": "my-user-id"
    }
  }
}
```


### 7.2. Pointset Exchange
Interaction with a device's points (e.g., sensors and setpoints).

#### Action: Publish Config Update
Updating the `room_temperature` setpoint for device `BLD-1`.

**PubSub Attributes:**
```json
{
  "projectId": "my-gcp-project",
  "deviceRegistryId": "my-managed-registry",
  "deviceId": "BLD-1",
  "subFolder": "pointset",
  "subType": "config",
  "transactionId": "UUFI:sess123:002",
  "nonce": "e5f6a7b8",
  "source": "my-user-id",
  "principal": "my-user-id@"
}
```

**PubSub Data (JSON):**
```json
{
  "version": "1.5.2",
  "timestamp": "2026-04-29T10:05:00Z",
  "pointset": {
    "points": {
      "room_temperature": {
        "set_value": 22.5
      }
    }
  }
}
```

#### Action: Receive Telemetry Event
Receiving the current `room_temperature` reading from device `BLD-1`.

**PubSub Attributes:**
```json
{
  "projectId": "my-gcp-project",
  "deviceRegistryId": "my-managed-registry",
  "deviceId": "BLD-1",
  "subFolder": "pointset",
  "subType": "events",
  "nonce": "c9d0e1f2",
  "publishTime": "2026-04-29T10:06:00Z"
}
```

**PubSub Data (JSON):**
```json
{
  "version": "1.5.2",
  "timestamp": "2026-04-29T10:06:00Z",
  "pointset": {
    "points": {
      "room_temperature": {
        "present_value": 22.1
      }
    }
  }
}
```

### 7.3. MQTT Examples
The following examples demonstrate the same operations using the MQTT transport, following the rule that topic-encoded fields are omitted from the payload.

#### Example: Handshake State (Publish)
Using generic addressing for the initial handshake.

**Topic:** `/uufi/p/{principal}/c/state/udmi`

**Payload (JSON):**
```json
{
  "transactionId": "UUFI:sess123:001",
  "nonce": "a1b2c3d4",
  "source": "my-user-id",
  "principal": "my-user-id",
  "payload": {
    "version": "1.5.2",
    "timestamp": "2026-04-29T10:00:00Z",
    "udmi": {
      "setup": {
        "functions_ver": 9,
        "transaction_id": "UUFI:sess123:001",
        "msg_source": "my-user-id",
        "user": "my-user-id"
      }
    }
  }
}
```

#### Example: Pointset Config (Publish)
Updating device `BLD-1`.

**Topic:** `/uufi/r/my-managed-registry/d/BLD-1/c/config/pointset`

**Payload (JSON):**
```json
{
  "transactionId": "UUFI:sess123:002",
  "nonce": "e5f6a7b8",
  "source": "my-user-id",
  "principal": "my-user-id",
  "payload": {
    "version": "1.5.2",
    "timestamp": "2026-04-29T10:05:00Z",
    "pointset": {
      "points": {
        "room_temperature": {
          "set_value": 22.5
        }
      }
    }
  }
}
```

## 8. Reliability and Error Handling

### MQTT Quality of Service
To ensure reliable delivery of state and configuration messages, all MQTT communications SHOULD use **QoS 1** (At Least Once).

### Error Reporting
When the System encounters an error processing a UUFI message, it will respond via the reply channel using the `error` subType.
The payload will include:
- `category`: A string describing the error type. All components MUST use standardized categories as defined in the [UDMI Categories Specification](https://github.com/faucetsdn/udmi/blob/master/docs/specs/categories.md) (e.g., `system.config.parse`, `system.auth.error`, `validation.error`).
- `message`: A human-readable description of the error.
- `transactionId`: The ID of the message that caused the error (if available).

## 9. Compliance and Common Pitfalls

Integration testing between different implementations has identified common areas of non-compliance. Implementations MUST adhere to the following to ensure interoperability:

### 9.1. Mandatory Payload Fields
Every message's inner `payload` object MUST contain `timestamp` and `version` fields.
- **Payload Structure:** The `payload` object MUST contain exactly one top-level key matching the `subFolder` name (e.g., `system`, `pointset`, `update`, `cloud`), which contains the UDMI data, in addition to the mandatory `timestamp` and `version` fields at the same level.
    - **Cloud Specifics:** For `cloud` subfolder messages, the UDMI payload MUST be wrapped in a top-level `cloud` key (e.g., `{"version": "...", "timestamp": "...", "cloud": { ... }}`). This ensures consistent parsing across all subfolders and prevents implementations from accidentally sending model data at the root of the message.
    - **Protocol Version:** The top-level `payload.version` field MUST ONLY reflect the UUFI Protocol Version (e.g., `1.5.2`). It MUST NOT be used to report device firmware versions.
- **Field Consistency:**
    - **Current Version:** Devices MUST report their active firmware version using the `current_version` field within the inner `state` data. It MUST NOT use the top-level `version` field for this purpose.
    - **LKG Version:** Devices MUST report their most recent verified operational version using the `lkg_version` field.
    - **Operation Status:** Devices MUST report their operational state (e.g., `quiescent`, `pending`, `success`, `failure`) using the `status` field.
- **Guidance:** Ensure `publishTime` is in the envelope and `timestamp` is in the inner payload. Ensure `version` (protocol) and `lkg_version` (firmware) are present in the payload. Use the subfolder wrapper for all UDMI fields.

### 9.2. Handshake Addressing
The `/uufi/p/{principal}/c/` topic branch MUST be used for the initial handshake.
- **Strict Prefix:** The handshake topic MUST exactly match the `/uufi/p/{principal}/c/{subType}/{subFolder}` pattern to ensure early-session identification.
- **Guidance:** Reserve `/uufi/r/` for post-handshake, registry-associated traffic. Ensure all clients use the same `c/` channel for handshakes and rely on envelope-based identification.

### 9.3. Envelope Redundancy
Top-level envelope fields MUST only include data NOT already encoded in the MQTT topic structure.
- **Principal Exception:** While the `principal` is encoded in the MQTT topic path for registry-less topics, it SHOULD also be included in the outer JSON envelope for all registry-less messages to facilitate easier filtering by passive observers and multi-session responders.
- **Guidance:** Maintain a clean inner UDMI message by omitting redundant fields like `deviceId` or `subType` from the outer JSON wrap.

### 9.4. Timestamp Format
All timestamps MUST follow RFC 3339 in the **minimal precision format** (e.g., `2026-05-01T22:32:17Z`). 
- **UTC Mandate:** Implementations MUST use UTC and MUST use the `Z` suffix (e.g., `2026-05-01T22:32:17Z`). 
- **Strictness:** Microseconds or numeric time zone offsets (e.g., `+00:00`) MUST NOT be used when generating messages. 

**Permissiveness Rule:**
All components MUST be strict in what they send (minimal precision only with `Z` suffix) but SHOULD be permissive in what they receive (handling microseconds or offsets gracefully).

### 9.5. Model Storage Consistency
While internal storage format is an implementation detail, tools sharing a Model Repository (e.g., `register`, `trigger`, and `mocket`) MUST agree on the schema.
- **Mandatory Format:** For maximum interoperability, it is RECOMMENDED that the internal storage (e.g., `model.json`) uses the same nested `registries` structure defined in Section 5.1.
- **Initialization:** Components MUST NOT assume the storage file is empty or pre-initialized with a specific structure. Use robust JSON parsing and ensure mandatory top-level keys (like `registries`) exist before operation.

### 9.6. Multi-Registry Support
All components MUST support multi-registry environments. 
- **Keys:** State tracking MUST use a composite key of `registry_id` and `device_id`.
- **Flat Structures:** Implementations MUST NOT use a flat `devices` map at the root of the model, as this prevents supporting devices with the same ID in different registries.
