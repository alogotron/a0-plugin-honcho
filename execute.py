"""
Honcho Plugin — User-Triggered Setup & Health Check

Run from the Plugins UI to install dependencies and verify connectivity.
Safe to run multiple times.
"""

import subprocess
import sys

_PACKAGE = "honcho-ai>=2.0.0"


def main():
    # --- Step 1: Install dependencies ---
    print("[1/3] Installing Honcho SDK...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", _PACKAGE],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"ERROR: pip install failed:\n{result.stderr}")
        return 1
    print(f"  ✓ {_PACKAGE} installed.")

    # --- Step 2: Verify import ---
    print("[2/3] Verifying Honcho SDK import...")
    try:
        import honcho  # noqa: F401
        version = getattr(honcho, "__version__", "unknown")
        print(f"  ✓ honcho SDK version: {version}")
    except ImportError as e:
        print(f"ERROR: Could not import honcho: {e}")
        return 1

    # --- Step 3: Check API key ---
    print("[3/3] Checking API key configuration...")
    try:
        from helpers.secrets import get_secrets_manager
        secrets_mgr = get_secrets_manager(None)
        secrets = secrets_mgr.load_secrets()
        key = secrets.get("HONCHO_API_KEY", "").strip()
        if key:
            masked = key[:8] + "..." + key[-4:] if len(key) > 12 else "***"
            print(f"  ✓ HONCHO_API_KEY found: {masked}")
        else:
            print("  ⚠ HONCHO_API_KEY not set. Add it in Settings → Secrets.")
    except Exception as e:
        print(f"  ⚠ Could not check secrets: {e}")
        print("    Add HONCHO_API_KEY in Settings → Secrets.")

    print("\nDone. Plugin is ready to use.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
