import os
import sys

workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Prepend our sandboxed bin directory to PATH and LD_LIBRARY_PATH
sandboxed_bin = os.path.join(workspace_root, "tmp", "bin")
os.environ["PATH"] = f"{sandboxed_bin}:{os.environ.get('PATH', '')}"
os.environ["LD_LIBRARY_PATH"] = f"{sandboxed_bin}:{os.environ.get('LD_LIBRARY_PATH', '')}"

# Verify impl/udmi layout on startup as required by spec/butler.md Section 10
udmi_dir = os.path.join(workspace_root, 'impl', 'udmi')
if not os.path.isdir(udmi_dir):
    sys.stderr.write(f"Hard Fail: Cloned UDMI directory not found at {udmi_dir}\n")
    sys.exit(1)
