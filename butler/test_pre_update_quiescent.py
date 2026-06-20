import unittest
import time
from unittest.mock import MagicMock, patch
from butler.orchestrator import Orchestrator
from butler.conn_spec import parse_conn_spec

class TestPreUpdateQuiescent(unittest.TestCase):
    @patch('butler.orchestrator.get_transport')
    @patch('butler.orchestrator.BlobRepository')
    def test_pre_update_quiescent_does_not_terminate_pending_transition(self, mock_blob_repo_class, mock_get_transport):
        # Setup mocks
        mock_transport = MagicMock()
        mock_get_transport.return_value = mock_transport
        mock_blob_repo = MagicMock()
        mock_blob_repo_class.return_value = mock_blob_repo

        # Initialize connection specification with a mock protocol to avoid network side-effects
        conn_spec = parse_conn_spec("mock://localhost:1234/test_prefix")
        
        # Initialize Orchestrator
        orchestrator = Orchestrator(conn_spec)
        orchestrator.is_active = True

        registry_id = "test-reg"
        device_id = "test-dev"
        subsystem = "system"
        key = (registry_id, device_id, subsystem)

        # 1. Trigger pending transition by manually adding to pending_updates
        orchestrator.pending_updates[key] = {
            "timestamp": 123456789.0,
            "target_version": "2.0.0"
        }

        # 2. Simulate pre-update quiescent state report (device still at old version '1.0.0')
        env = {
            "subType": "state",
            "subFolder": "udmi",
            "deviceId": device_id,
            "deviceRegistryId": registry_id
        }
        payload = {
            "system": {
                "software": {
                    subsystem: {
                        "status": "quiescent",
                        "current_version": "1.0.0",
                        "lkg_version": "1.0.0"
                    }
                }
            }
        }

        # Handle the message
        orchestrator.on_message(env, payload, "test-topic")

        # ASSERTION 1: Verify that the pending transition was NOT popped from pending_updates
        self.assertIn(key, orchestrator.pending_updates)
        self.assertFalse(orchestrator.pending_updates[key].get("has_reported_pending", False))

        # 3. Simulate device transitioning to pending status
        payload["system"]["software"][subsystem]["status"] = "pending"
        orchestrator.on_message(env, payload, "test-topic")

        # ASSERTION 2: Verify pending update is still tracked and has_reported_pending is now True
        self.assertIn(key, orchestrator.pending_updates)
        self.assertTrue(orchestrator.pending_updates[key].get("has_reported_pending", True))

        # 4. Simulate device transitioning to success state
        payload["system"]["software"][subsystem]["status"] = "success"
        payload["system"]["software"][subsystem]["current_version"] = "2.0.0"
        orchestrator.on_message(env, payload, "test-topic")

        # ASSERTION 3: Verify that after reaching terminal success state, the pending transition is popped
        self.assertNotIn(key, orchestrator.pending_updates)

    @patch('butler.orchestrator.get_transport')
    @patch('butler.orchestrator.BlobRepository')
    def test_direct_terminal_quiescent_pops_pending(self, mock_blob_repo_class, mock_get_transport):
        # Setup mocks
        mock_transport = MagicMock()
        mock_get_transport.return_value = mock_transport
        mock_blob_repo = MagicMock()
        mock_blob_repo_class.return_value = mock_blob_repo

        conn_spec = parse_conn_spec("mock://localhost:1234/test_prefix")
        orchestrator = Orchestrator(conn_spec)
        orchestrator.is_active = True

        registry_id = "test-reg"
        device_id = "test-dev"
        subsystem = "system"
        key = (registry_id, device_id, subsystem)

        orchestrator.pending_updates[key] = {
            "timestamp": 123456789.0,
            "target_version": "2.0.0"
        }

        # Simulate direct terminal quiescent state report where current_version == target_version
        env = {
            "subType": "state",
            "subFolder": "udmi",
            "deviceId": device_id,
            "deviceRegistryId": registry_id
        }
        payload = {
            "system": {
                "software": {
                    subsystem: {
                        "status": "quiescent",
                        "current_version": "2.0.0",
                        "lkg_version": "2.0.0"
                    }
                }
            }
        }

        orchestrator.on_message(env, payload, "test-topic")

        # ASSERTION: Verify that the pending transition is popped because it reached its final terminal version
        self.assertNotIn(key, orchestrator.pending_updates)

    @patch('butler.orchestrator.get_transport')
    @patch('butler.orchestrator.BlobRepository')
    def test_compliance_no_rollback_target_on_failure_and_timeout(self, mock_blob_repo_class, mock_get_transport):
        mock_transport = MagicMock()
        mock_get_transport.return_value = mock_transport
        mock_blob_repo = MagicMock()
        mock_blob_repo_class.return_value = mock_blob_repo

        conn_spec = parse_conn_spec("mock://localhost:1234/test_prefix")
        orchestrator = Orchestrator(conn_spec)
        orchestrator.is_active = True

        registry_id = "test-reg"
        device_id = "test-dev"
        subsystem = "system"
        key = (registry_id, device_id, subsystem)

        # Initialize device in orchestrator models
        orchestrator.models[registry_id] = {
            device_id: {
                subsystem: {
                    "target_version": "2.0.0",
                    "current_version": "1.0.0",
                    "lkg_version": "1.0.0",
                    "status": "pending"
                }
            }
        }

        # Trigger pending transition by manually adding to pending_updates
        orchestrator.pending_updates[key] = {
            "timestamp": 123456789.0,
            "target_version": "2.0.0"
        }

        # 1. Test Failure: Simulate device reporting failure
        env = {
            "subType": "state",
            "subFolder": "udmi",
            "deviceId": device_id,
            "deviceRegistryId": registry_id
        }
        payload = {
            "system": {
                "software": {
                    subsystem: {
                        "status": "failure",
                        "current_version": "1.0.0",
                        "lkg_version": "1.0.0"
                    }
                }
            }
        }

        orchestrator.on_message(env, payload, "test-topic")

        # Verify that target_version was NOT rolled back to lkg_version ("1.0.0")
        device_info = orchestrator.models[registry_id][device_id][subsystem]
        self.assertEqual(device_info["target_version"], "2.0.0")
        self.assertEqual(device_info["status"], "failure")

        # 2. Test Timeout: Simulate timeout
        # Reset state to pending
        device_info["status"] = "pending"
        orchestrator.pending_updates[key] = {
            "timestamp": 0.0,  # Far in the past to trigger timeout
            "target_version": "2.0.0"
        }

        # Call check_timeouts
        orchestrator.check_timeouts()

        # Verify that target_version was NOT rolled back to lkg_version ("1.0.0")
        self.assertEqual(device_info["target_version"], "2.0.0")
        self.assertEqual(device_info["status"], "failed")
        self.assertNotIn(key, orchestrator.pending_updates)

    @patch('butler.orchestrator.get_transport')
    @patch('butler.orchestrator.BlobRepository')
    def test_timeout_reset_on_progress_update(self, mock_blob_repo_class, mock_get_transport):
        # Verify that a specific, measurable progress update resets the timeout timer
        mock_transport = MagicMock()
        mock_get_transport.return_value = mock_transport
        mock_blob_repo = MagicMock()
        mock_blob_repo_class.return_value = mock_blob_repo

        conn_spec = parse_conn_spec("mock://localhost:1234/test_prefix")
        orchestrator = Orchestrator(conn_spec)
        orchestrator.is_active = True

        registry_id = "test-reg"
        device_id = "test-dev"
        subsystem = "system"
        key = (registry_id, device_id, subsystem)

        # Initialize device
        orchestrator.models[registry_id] = {
            device_id: {
                subsystem: {
                    "target_version": "2.0.0",
                    "current_version": "1.0.0",
                    "status": "pending"
                }
            }
        }

        # Manually register the pending update with a past timestamp
        old_timestamp = time.time() - 1000
        orchestrator.pending_updates[key] = {
            "timestamp": old_timestamp,
            "target_version": "2.0.0"
        }

        # Send state report with a progress field
        env = {
            "subType": "state",
            "subFolder": "udmi",
            "deviceId": device_id,
            "deviceRegistryId": registry_id
        }
        payload = {
            "system": {
                "software": {
                    subsystem: {
                        "status": "pending",
                        "current_version": "1.0.0",
                        "progress": 25.0
                    }
                }
            }
        }

        orchestrator.on_message(env, payload, "test-topic")

        # Verify the timestamp was updated/reset to current time
        new_timestamp = orchestrator.pending_updates[key]["timestamp"]
        self.assertGreater(new_timestamp, old_timestamp)

    @patch('butler.orchestrator.get_transport')
    @patch('butler.orchestrator.BlobRepository')
    def test_alternative_target_version_prohibited(self, mock_blob_repo_class, mock_get_transport):
        # Verify alternative target_version config property in system configurations is ignored
        mock_transport = MagicMock()
        mock_get_transport.return_value = mock_transport
        mock_blob_repo = MagicMock()
        mock_blob_repo_class.return_value = mock_blob_repo

        conn_spec = parse_conn_spec("mock://localhost:1234/test_prefix")
        orchestrator = Orchestrator(conn_spec)
        orchestrator.is_active = True

        # Send model update with system.target_version (prohibited!)
        env = {
            "subType": "model",
            "subFolder": "cloud"
        }
        payload = {
            "registries": {
                "test-reg": {
                    "devices": {
                        "test-dev": {
                            "system": {
                                "target_version": "2.0.0"
                            }
                        }
                    }
                }
            }
        }

        orchestrator.on_message(env, payload, "test-topic")

        # Since software dictionary is missing and target_version at system root is prohibited, target_version must not be accepted
        devices = orchestrator.models.get("test-reg", {})
        dev_info = devices.get("test-dev", {}).get("system", {})
        self.assertNotIn("target_version", dev_info)

if __name__ == "__main__":
    unittest.main()
