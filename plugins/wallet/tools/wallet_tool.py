"""wallet_tool — EOA wallet operations for wonder1.

Three operations:
  get_balance              — fetch ETH and optional ERC-20 token balances
  sign_and_send_transaction — sign and broadcast a transaction via viem
  get_transaction_status   — check confirmation status of a tx hash

All signing uses WALLET_PRIVATE_KEY read from environment.
All RPC calls target WALLET_RPC_URL (default: Base mainnet public RPC).
No private key is ever logged, stored, or returned in results.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

from helpers.tool import Tool, Response

# Absolute path to the Node.js wallet script bundled with this plugin
_PLUGIN_DIR = Path(__file__).parent.parent
_WALLET_SCRIPT = _PLUGIN_DIR / "scripts" / "wallet.js"

# How long to wait for a single RPC call (seconds)
_TIMEOUT_SECONDS = 60


class WalletTool(Tool):
    """Blockchain wallet tool — EOA signing and balance queries via viem."""

    async def execute(self, **kwargs) -> Response:
        operation = kwargs.get("operation", "").strip()

        if not operation:
            return Response(
                message="wallet_tool error: 'operation' argument is required. "
                        "Valid values: get_balance, sign_and_send_transaction, get_transaction_status",
                break_loop=False,
            )

        # Guard: never echo the private key back to the agent
        if "private_key" in kwargs or "privateKey" in kwargs:
            return Response(
                message="wallet_tool security error: do not pass private keys as tool arguments. "
                        "The key is read from WALLET_PRIVATE_KEY environment variable only.",
                break_loop=False,
            )

        # Build the payload for the Node.js script
        payload = {"operation": operation, "args": {k: v for k, v in kwargs.items() if k != "operation"}}

        result = self._call_node(payload)
        return Response(message=result, break_loop=False)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_node(self, payload: dict) -> str:
        """Invoke wallet.js as a subprocess and return its JSON output as a formatted string."""
        if not _WALLET_SCRIPT.exists():
            return (
                f"wallet_tool error: wallet.js not found at {_WALLET_SCRIPT}. "
                "Run 'npm install' inside the plugin's scripts/ directory."
            )

        env = os.environ.copy()
        # Ensure node_modules installed next to wallet.js is on the path
        node_modules_bin = _PLUGIN_DIR / "scripts" / "node_modules" / ".bin"
        env["PATH"] = str(node_modules_bin) + ":" + env.get("PATH", "")

        try:
            proc = subprocess.run(
                ["node", str(_WALLET_SCRIPT)],
                input=json.dumps(payload),
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return f"wallet_tool error: Node.js script timed out after {_TIMEOUT_SECONDS}s"
        except FileNotFoundError:
            return "wallet_tool error: 'node' executable not found. Install Node.js (v18+)."

        stdout = proc.stdout.strip()
        stderr = proc.stderr.strip()

        if proc.returncode != 0:
            error_detail = stderr or stdout or "(no output)"
            return f"wallet_tool error (exit {proc.returncode}): {error_detail}"

        # Parse and pretty-print the JSON result from Node
        try:
            result = json.loads(stdout)
            # Strip any accidental private key leak in the result (belt-and-suspenders)
            result.pop("privateKey", None)
            result.pop("private_key", None)
            return json.dumps(result, indent=2)
        except json.JSONDecodeError:
            return f"wallet_tool: unexpected non-JSON output from wallet.js:\n{stdout}"
