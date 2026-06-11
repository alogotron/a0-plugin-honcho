## wallet_tool

EOA wallet operations for wonder1. Uses viem (Node.js) under the hood.
Signing key is selected from environment — never pass keys as arguments.
Default chain: **Base mainnet** (chainId 8453). Override RPC via `WALLET_RPC_URL`.

### Per-agent key selection

Pass an optional `"agent"` argument to select a specific wallet:
- `"alpha"` → uses `AGENT_ALPHA_PRIVATE_KEY` / `AGENT_ALPHA_ADDRESS`
- `"beta"` → uses `AGENT_BETA_PRIVATE_KEY` / `AGENT_BETA_ADDRESS`
- `"gamma"` → uses `AGENT_GAMMA_PRIVATE_KEY` / `AGENT_GAMMA_ADDRESS`
- `"delta"` → uses `AGENT_DELTA_PRIVATE_KEY` / `AGENT_DELTA_ADDRESS`

Omit `"agent"` to fall back to `WALLET_PRIVATE_KEY` (default behaviour).

When `"agent"` is set and no explicit `"address"` is provided, `get_balance` also resolves the address from the corresponding `AGENT_<NAME>_ADDRESS` env var automatically.

### Usage

Call with `"tool_name": "wallet_tool"` and a mandatory `"operation"` arg plus operation-specific args.

---

#### `get_balance` — Check ETH and token balances

```json
{
  "tool_name": "wallet_tool",
  "tool_args": {
    "operation": "get_balance",
    "address": "0xYourAddressHere"
  }
}
```

Optional: check ERC-20 tokens alongside ETH:
```json
{
  "tool_name": "wallet_tool",
  "tool_args": {
    "operation": "get_balance",
    "address": "0xYourAddressHere",
    "tokens": [
      { "address": "0xTokenContractAddress", "symbol": "USDC", "decimals": 6 }
    ]
  }
}
```

Omit `address` to use the address derived from `WALLET_PRIVATE_KEY`.

Returns: `{ address, chain, chainId, eth: { raw, formatted }, tokens: [...] }`

---

#### `sign_and_send_transaction` — Sign and broadcast a transaction

```json
{
  "tool_name": "wallet_tool",
  "tool_args": {
    "operation": "sign_and_send_transaction",
    "to": "0xRecipientOrContractAddress",
    "value": "0.001",
    "data": "0x"
  }
}
```

Args:
- `to` (required): recipient or contract address
- `value` (optional): ETH amount as decimal string e.g. `"0.001"`, or omit for `0`
- `data` (optional): hex-encoded calldata for contract interactions, default `"0x"`
- `gas` (optional): gas limit as string
- `gasPrice` (optional): gas price in wei as string
- `nonce` (optional): override nonce

Returns: `{ txHash, from, to, value, valueEth, status: "broadcast", note }`

**Always call `get_balance` first to confirm gas availability.**
**Always call `get_transaction_status` after to confirm mining.**
**Requires WALLET_PRIVATE_KEY in environment — checks autonomy tier before calling.**

---

#### `get_transaction_status` — Check if a transaction confirmed

```json
{
  "tool_name": "wallet_tool",
  "tool_args": {
    "operation": "get_transaction_status",
    "txHash": "0xYourTransactionHashHere"
  }
}
```

Returns one of:
- `status: "confirmed"` — mined successfully, includes `blockNumber`, `gasUsed`
- `status: "reverted"` — mined but execution failed
- `status: "pending"` — in mempool, not yet mined
- `status: "not_found"` — hash not found (check or wait)

---

### Security Rules (wonder1 must follow these always)

1. **Never pass `WALLET_PRIVATE_KEY` as a tool argument** — it is environment-only
2. **Check balance before every send** — insufficient gas causes stuck transactions
3. **Confirm after every send** — poll `get_transaction_status` until `confirmed` or `reverted`
4. **Apply autonomy tier** before calling `sign_and_send_transaction` — escalate if uncertain
5. **Log every transaction** using the standard log format after the status is known
