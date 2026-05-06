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
Run the setup script to prepare the communication bus. Note that this script will attempt to start the `mosquitto` broker if it is not already running on the default port (1883):
```bash
bin/setup mqtt://localhost
```
**Expected behavior:** The script should output "Bus setup complete."

## Manual Operation

To run the components individually and observe their behavior:

### 1. Start the Orchestrator
```bash
bin/butler mqtt://localhost
```
**Expected behavior:** The orchestrator starts and waits for state changes. It will periodically check the model for required updates.

### 2. Start the Verifier (Optional)
The verifier monitors the bus and reports on the success or failure of update sequences.
```bash
bin/verifier mqtt://localhost
```
**Expected behavior:** The verifier starts listening to the MQTT bus. It will output verification results to the bus.

### 3. Start an Observer (Optional)
Watch the JSON message traffic in real-time:
```bash
bin/observe mqtt://localhost
```
**Expected behavior:** The observer will print formatted JSON messages for all traffic on the bus.

### 4. Register and Start a Mock Device
In a new terminal:
```bash
# Register the device in the model
bin/register my-registry dev-001

# Start the mock device
bin/mocket mqtt://localhost my-registry dev-001
```
**Expected behavior:** 
- `bin/register` will output "Registered device dev-001 in model."
- `bin/mocket` will start and begin publishing periodic status messages. You should see these messages appearing in the **Observer** window.

### 5. Trigger an Update
Create a dummy firmware file and trigger an update for the device:
```bash
echo "V1.1 CONTENT" > fw_v1.1.0.bin
bin/trigger my-registry dev-001 1.1.0 fw_v1.1.0.bin
```
**Expected behavior:** 
- `bin/trigger` will report that the blob was stored and the target version was updated.
- In the **Observer**, you should see an `update_payload` message sent from `butler` to `dev-001`.
- In the **Mocket** output, you should see the device receiving the update, downloading the blob, and reporting `success`.
- Finally, the **Butler** logs will show it updating the `current_version` in the model.

## Testing

### Smoke Test
Run a quick end-to-end smoke test that verifies basic component connectivity:
```bash
bin/smokeit mqtt://localhost
```
**Expected behavior:** The script will launch temporary instances of the system components, run a sample update, and output "Smoke test passed" (or a detailed error if something is misconfigured).

## Documentation
For detailed architectural specifications and component requirements, see:
- `T01_Butler_Managed_Update_System_Architecture.md`
- `AGENTS.md` (Project-specific hints)
