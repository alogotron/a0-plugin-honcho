# ERC-8004 Reputation Farming

**Version**: 0.1.0-stub  
**Target**: https://8004scan.io/  
**Agent**: wonder1 only  
**Chain**: Base mainnet (chainId 8453)  
**Status**: Stub — interaction patterns to be filled in after researching the live site

---

## Trigger Phrases

- "farm ERC-8004 reputation"
- "check 8004scan reputation"
- "register on 8004scan"
- "run ERC-8004 session"
- "what is wonder1's reputation score"

---

## Overview

ERC-8004 is an on-chain reputation protocol. 8004scan.io is the primary explorer and interaction surface. wonder1's goal is to build a measurable reputation score by executing protocol-approved interactions over time.

**Always use `wallet_tool` for all on-chain actions. Never attempt raw transactions outside the wallet plugin.**

---

## Pre-Session Checklist

Before any session, wonder1 must:

1. Query Honcho memory for `wonder1.ecosystem.erc8004.registered` — skip registration if already done
2. Call `wallet_tool get_balance` — confirm sufficient ETH for gas
3. Check `wonder1.ecosystem.erc8004.reputation` from memory — log baseline score
4. Check `wonder1.session.last_log` — review what was done last session to avoid duplication

---

## Section 1: Registration Flow

> **Status**: To be researched — visit https://8004scan.io/ to extract registration steps

**Placeholder — fill in after live research:**

- [ ] What wallet connection method does 8004scan.io use? (WalletConnect, injected, etc.)
- [ ] Is there a registration transaction? What contract? What calldata?
- [ ] Is registration free or does it require a fee/deposit?
- [ ] What is the contract address for the ERC-8004 registry?
- [ ] What event is emitted on successful registration?
- [ ] How do you verify registration succeeded? (contract read, API, explorer?)

**Once researched, document here:**
```
Registry contract: TBD
Registration function: TBD
Typical gas cost: TBD
Verification method: TBD
```

**wonder1 protocol once known:**
1. Check if already registered via contract read
2. If not: apply Supervised autonomy tier (new contract, first interaction)
3. Surface registration plan to owner, wait for approval
4. Execute registration via `wallet_tool sign_and_send_transaction`
5. Confirm via `wallet_tool get_transaction_status`
6. Write `wonder1.ecosystem.erc8004.registered = true` to Honcho memory

---

## Section 2: Reputation-Building Actions

> **Status**: To be researched — what actions increment reputation on 8004scan.io?

**Placeholder — fill in after live research:**

- [ ] What types of actions build reputation? (attestations, interactions, votes, activity?)
- [ ] Are there daily/weekly action limits or cooldowns?
- [ ] What is the reputation scoring formula or weighting?
- [ ] Are there special multiplier events or boosted-reputation windows?
- [ ] Is reputation soulbound or transferable?
- [ ] Does multi-chain activity count, or Base only?

**Action catalog (fill in):**

| Action | Contract | Function | Gas Estimate | Reputation Value | Cooldown | Autonomy Tier |
|---|---|---|---|---|---|---|
| TBD | TBD | TBD | TBD | TBD | TBD | TBD |

**Candidate daily routine once known:**
```
1. [action_1] — estimated gas: TBD
2. [action_2] — estimated gas: TBD
3. Read back reputation score
4. Log session summary to Honcho
```

---

## Section 3: Transaction Patterns

> **Status**: To be researched — concrete calldata and ABIs needed

**Placeholder — fill in after live research:**

### 3.1 Reading Reputation Score

```javascript
// Example pattern — replace with real contract/ABI after research
// Contract: TBD
// Function: reputation(address) → uint256
// Call via wallet_tool get_balance (read-only) or direct publicClient.readContract
```

### 3.2 Attestation (example pattern)

```json
// Example wallet_tool call — replace with real calldata after research
{
  "tool_name": "wallet_tool",
  "tool_args": {
    "operation": "sign_and_send_transaction",
    "to": "0xTBD_CONTRACT_ADDRESS",
    "value": "0",
    "data": "0xTBD_FUNCTION_SELECTOR_AND_CALLDATA"
  }
}
```

### 3.3 Known Contract Addresses (fill in)

```
ERC-8004 Registry:     TBD
Reputation Oracle:     TBD
Attestation Contract:  TBD
Other (TBD):           TBD
```

---

## Section 4: Monitoring & Scoring

> **Status**: Placeholder — update after identifying the reputation read path

- **Score check**: TBD — direct contract read or 8004scan.io API?
- **Explorer link**: https://8004scan.io/ (wonder1's address: stored in Honcho as `wonder1.wallet.address`)
- **Target milestone**: Non-trivial score within 30 days of registration

Wonder1 should log current reputation score at the end of every session:
```
[DATE UTC] REPUTATION_CHECK | score: TBD | delta: +N | source: TBD
```

---

## Section 5: Research Tasks (Next Steps)

Before this skill is operational, complete these research steps:

1. **Browse 8004scan.io** — identify the UI flow: registration, actions, score display
2. **Inspect contract interactions** — use block explorer (basescan.org) to find real contract addresses and function signatures used by the frontend
3. **Check ERC-8004 spec** — find the EIP or GitHub repo defining the standard
4. **Check slop-computer-wallet** — confirm ERC-1271 compatibility with ERC-8004 attestations (relevant for Layer 2 upgrade)
5. **Fill in all TBD sections above** — then remove the stub status

---

## Sybil Risk Notes

- wonder1 is a single agent with a single wallet — not a sybil farm
- Automated activity may still be flagged by some protocols
- Monitor 8004scan.io for any anti-bot signals or warnings
- If flagged: pause autonomous sessions, escalate to owner

---

## Memory Keys for This Ecosystem

| Key | Value |
|---|---|
| `wonder1.ecosystem.erc8004.registered` | `true` / `false` |
| `wonder1.ecosystem.erc8004.reputation` | Last known score (number) |
| `wonder1.ecosystem.erc8004.wallet` | wonder1's EOA address |
| `wonder1.ecosystem.erc8004.last_action` | ISO timestamp of last on-chain action |
| `wonder1.ecosystem.erc8004.contracts` | JSON map of known contract addresses |
