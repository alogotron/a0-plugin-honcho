# wallet — EOA Wallet Plugin for wonder1

A minimal A0 plugin providing three on-chain operations for wonder1.
Built with [viem](https://viem.sh/) (Node.js). Targets **Base mainnet** (chainId 8453) by default.

## Architecture

```
wonder1 agent
    └── wallet_tool (Python)
            └── wallet.js (Node.js / viem)
                    └── Base RPC endpoint
```

The Python tool (`tools/wallet_tool.py`) serializes the operation and args to JSON,
spawns `wallet.js` as a subprocess, and parses the JSON result.
The Node.js script (`scripts/wallet.js`) handles all blockchain I/O via viem.

## Operations

| Operation | Description |
|---|---|
| `get_balance` | Fetch ETH balance (+ optional ERC-20 tokens) for a wallet address |
| `sign_and_send_transaction` | Sign and broadcast a transaction via EOA private key |
| `get_transaction_status` | Check if a tx hash has been mined (confirmed / pending / reverted) |

See `prompts/agent.system.tool.wallet_tool.md` for full call schema.

## Configuration

All secrets are environment-only. Set these in `secrets.env`:

| Variable | Required | Description |
|---|---|---|
| `WALLET_PRIVATE_KEY` | For signing | 0x-prefixed 32-byte hex EOA private key |
| `WALLET_RPC_URL` | Optional | Override Base RPC. Default: `https://mainnet.base.org` |

**Never hardcode keys. Never log the private key. Never pass it as a tool argument.**

## Setup

```bash
# Install Node dependencies (run once)
cd .a0proj/plugins/wallet/scripts
npm install
```

## Layer 2 Upgrade Path (Planned)

This plugin implements **Layer 1 — raw EOA signing** only.

**Layer 2** will add smart-contract multisig via [slop-computer-wallet](https://github.com/clawdbotatg/slop-computer-wallet):

- **1-of-2 signing** — wonder1 acts alone (autonomous mode)
- **2-of-2 signing** — wonder1 + owner passkey required (supervised mode)
- **ERC-1271** compatible — smart contract signature verification

When upgrading to Layer 2:
1. Deploy `slop-computer-wallet` contract and record the contract address
2. Add `WALLET_CONTRACT_ADDRESS` and `WALLET_MODE` (autonomous/supervised) to `secrets.env`
3. Update `wallet.js` to route `sign_and_send_transaction` through the multisig contract
4. Add a `deploy_wallet` operation for initial multisig setup

## Security Notes

- EOA private key stored in `secrets.env` — acceptable at experimental scale
- Plugin never logs or returns the private key
- All error messages are sanitized to redact long hex strings (potential key leaks)
- For production scale: upgrade to Layer 2 multisig for safer autonomous operation
