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
pip install -r requirements.txt
```

### 2. Start MQTT Broker
Ensure `mosquitto` is running in the background. If it's not running as a service:
```bash
mosquitto
```

### 3. Initialize the System
Run the setup script to prepare the communication bus. Note that this script will attempt to start the `mosquitto` broker if it is not already running on the default port (1883):
```bash
bin/setup
```

## Manual Operation

To run the components individually and observe their behavior:

### 1. Start the Orchestrator
```bash
bin/butler
```

### 2. Start the Verifier (Optional)
The verifier monitors the bus and reports on the success or failure of update sequences.
```bash
bin/verifier
```

### 3. Start an Observer (Optional)
Watch the JSON message traffic in real-time:
```bash
bin/observe
```

### 4. Register and Start a Mock Device
In a new terminal:
```bash
# Register the device in the model
bin/register dev-001

# Start the mock device
bin/mocket dev-001
```

### 5. Trigger an Update
Create a dummy firmware file and trigger an update for the device:
```bash
echo "V1.1 CONTENT" > fw_v1.1.0.bin
bin/trigger dev-001 1.1.0 fw_v1.1.0.bin
```

## Testing

### Smoke Test
Run a quick end-to-end smoke test that verifies basic component connectivity:
```bash
bin/smokeit
```

## Documentation
For detailed architectural specifications and component requirements, see:
- `T01_Butler_Managed_Update_System_Architecture.md`
- `AGENTS.md` (Project-specific hints)
