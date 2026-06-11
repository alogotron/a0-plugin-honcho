#!/usr/bin/env node
/**
 * wallet.js — EOA wallet operations for wonder1 (Layer 1)
 *
 * Transport: reads a JSON payload from stdin, writes a JSON result to stdout.
 * Chain:     Base mainnet (8453) by default; override with WALLET_RPC_URL env var.
 * Signing:   WALLET_PRIVATE_KEY env var (0x-prefixed 32-byte hex).
 *
 * Operations:
 *   get_balance              { address?, tokens?: [{address, symbol, decimals}] }
 *   sign_and_send_transaction { to, value?, data?, gas?, gasPrice?, nonce? }
 *   get_transaction_status   { txHash }
 *
 * Layer 2 note (planned upgrade path):
 *   This script implements Layer 1 — raw EOA signing only.
 *   Layer 2 will add smart-contract multisig via slop-computer-wallet
 *   (https://github.com/clawdbotatg/slop-computer-wallet) supporting
 *   1-of-2 autonomous mode and 2-of-2 supervised mode with passkey co-signer.
 *   When Layer 2 is integrated, update the sign/send path here to route through
 *   the multisig contract and add a deploy_wallet operation.
 */

import { createPublicClient, createWalletClient, http, formatEther, parseEther, isAddress } from 'viem';
import { privateKeyToAccount } from 'viem/accounts';
import { base } from 'viem/chains';

// ---------------------------------------------------------------------------
// Configuration — all from environment, never hardcoded
// ---------------------------------------------------------------------------

const RPC_URL = process.env.WALLET_RPC_URL || 'https://mainnet.base.org';
// ---------------------------------------------------------------------------
// Key resolution — per-agent or fallback to WALLET_PRIVATE_KEY
// ---------------------------------------------------------------------------

function resolvePrivateKey(agentName) {
  if (agentName) {
    const key = process.env[`AGENT_${agentName.toUpperCase()}_PRIVATE_KEY`] || '';
    if (key) return key;
    // fall through to default if agent key not found
  }
  return process.env.WALLET_PRIVATE_KEY || '';
}

// ---------------------------------------------------------------------------
// Client setup
// ---------------------------------------------------------------------------

function makeClients(agentName) {
  const transport = http(RPC_URL);
  const publicClient = createPublicClient({ chain: base, transport });

  const privateKey = resolvePrivateKey(agentName);
  if (!privateKey) {
    return { publicClient, walletClient: null, account: null };
  }

  // Normalize: ensure 0x prefix
  const normalizedKey = privateKey.startsWith('0x') ? privateKey : `0x${privateKey}`;
  const account = privateKeyToAccount(normalizedKey);
  const walletClient = createWalletClient({ account, chain: base, transport });

  return { publicClient, walletClient, account };
}

// ---------------------------------------------------------------------------
// Operations
// ---------------------------------------------------------------------------

/**
 * get_balance
 * Args: { address?: string, tokens?: Array<{address, symbol, decimals}> }
 * Returns native ETH balance plus optional ERC-20 token balances.
 */
async function getBalance(args, publicClient, account, agentName) {
  const agentEnvAddress = agentName
    ? process.env[`AGENT_${agentName.toUpperCase()}_ADDRESS`] || ''
    : '';
  const targetAddress = args.address || agentEnvAddress || account?.address;
  if (!targetAddress) {
    return { error: 'get_balance requires an address argument, an agent name, or a configured WALLET_PRIVATE_KEY' };
  }
  if (!isAddress(targetAddress)) {
    return { error: `Invalid address: ${targetAddress}` };
  }

  const weiBalance = await publicClient.getBalance({ address: targetAddress });
  const result = {
    address: targetAddress,
    chain: 'base',
    chainId: base.id,
    eth: {
      raw: weiBalance.toString(),
      formatted: formatEther(weiBalance),
    },
    tokens: [],
  };

  // ERC-20 balances (minimal balanceOf ABI)
  const erc20Abi = [
    { name: 'balanceOf', type: 'function', stateMutability: 'view',
      inputs: [{ name: 'owner', type: 'address' }],
      outputs: [{ name: '', type: 'uint256' }] },
  ];

  for (const token of (args.tokens || [])) {
    try {
      const raw = await publicClient.readContract({
        address: token.address,
        abi: erc20Abi,
        functionName: 'balanceOf',
        args: [targetAddress],
      });
      const decimals = token.decimals ?? 18;
      const divisor = 10n ** BigInt(decimals);
      const whole = raw / divisor;
      const frac = raw % divisor;
      const formatted = `${whole}.${frac.toString().padStart(decimals, '0').slice(0, 6)}`;
      result.tokens.push({ symbol: token.symbol, address: token.address, raw: raw.toString(), formatted });
    } catch (err) {
      result.tokens.push({ symbol: token.symbol, address: token.address, error: err.message });
    }
  }

  return result;
}

/**
 * sign_and_send_transaction
 * Args: { to, value?, data?, gas?, gasPrice?, nonce? }
 * Returns: { txHash, from, to, value, gasUsed? }
 *
 * Security:
 *  - Requires WALLET_PRIVATE_KEY env var
 *  - value is treated as ETH string (e.g. "0.001") and converted to wei
 *  - Set data to "0x" or omit for plain ETH transfers
 */
async function signAndSendTransaction(args, publicClient, walletClient, account) {
  if (!walletClient || !account) {
    return { error: 'sign_and_send_transaction requires WALLET_PRIVATE_KEY to be set in environment' };
  }
  if (!args.to || !isAddress(args.to)) {
    return { error: `Invalid or missing 'to' address: ${args.to}` };
  }

  // Parse value — accept ETH string or bigint-as-string (wei)
  let value = 0n;
  if (args.value !== undefined && args.value !== null && args.value !== '') {
    const valStr = String(args.value);
    // If it looks like a small decimal, treat as ETH; otherwise treat as wei
    value = valStr.includes('.') ? parseEther(valStr) : BigInt(valStr);
  }

  const txParams = {
    to: args.to,
    value,
    data: args.data || '0x',
  };
  if (args.gas) txParams.gas = BigInt(args.gas);
  if (args.gasPrice) txParams.gasPrice = BigInt(args.gasPrice);
  if (args.nonce !== undefined) txParams.nonce = Number(args.nonce);

  const txHash = await walletClient.sendTransaction(txParams);

  return {
    txHash,
    from: account.address,
    to: args.to,
    value: value.toString(),
    valueEth: formatEther(value),
    status: 'broadcast',
    note: 'Call get_transaction_status with this txHash to confirm mining',
  };
}

/**
 * get_transaction_status
 * Args: { txHash }
 * Returns: { txHash, status, blockNumber?, gasUsed?, effectiveGasPrice? }
 */
async function getTransactionStatus(args, publicClient) {
  if (!args.txHash) {
    return { error: "get_transaction_status requires 'txHash' argument" };
  }

  let receipt = null;
  try {
    receipt = await publicClient.getTransactionReceipt({ hash: args.txHash });
  } catch (_) {
    // Receipt not available yet = still pending
  }

  if (!receipt) {
    // Try to at least confirm it's in the mempool
    let tx = null;
    try {
      tx = await publicClient.getTransaction({ hash: args.txHash });
    } catch (_) {}

    if (!tx) {
      return { txHash: args.txHash, status: 'not_found',
        note: 'Transaction not found — check hash or wait if just broadcast' };
    }
    return { txHash: args.txHash, status: 'pending',
      from: tx.from, to: tx.to, value: tx.value?.toString(),
      note: 'Transaction is in mempool, not yet mined' };
  }

  const confirmed = receipt.status === 'success';
  return {
    txHash: args.txHash,
    status: confirmed ? 'confirmed' : 'reverted',
    blockNumber: receipt.blockNumber?.toString(),
    blockHash: receipt.blockHash,
    gasUsed: receipt.gasUsed?.toString(),
    effectiveGasPrice: receipt.effectiveGasPrice?.toString(),
    from: receipt.from,
    to: receipt.to,
    contractAddress: receipt.contractAddress || null,
  };
}

// ---------------------------------------------------------------------------
// Main — read stdin, dispatch, write stdout
// ---------------------------------------------------------------------------

async function main() {
  let raw = '';
  for await (const chunk of process.stdin) raw += chunk;

  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (err) {
    process.stdout.write(JSON.stringify({ error: `Invalid JSON input: ${err.message}` }));
    process.exit(1);
  }

  const { operation, args = {} } = payload;
  const agentName = args.agent || null;
  const { publicClient, walletClient, account } = makeClients(agentName);

  let result;
  try {
    switch (operation) {
      case 'get_balance':
        result = await getBalance(args, publicClient, account, agentName);
        break;
      case 'sign_and_send_transaction':
        result = await signAndSendTransaction(args, publicClient, walletClient, account);
        break;
      case 'get_transaction_status':
        result = await getTransactionStatus(args, publicClient);
        break;
      default:
        result = {
          error: `Unknown operation: '${operation}'. Valid: get_balance, sign_and_send_transaction, get_transaction_status`,
        };
    }
  } catch (err) {
    // Sanitize error message — strip any accidental key references
    const msg = err.message?.replace(/(0x[0-9a-fA-F]{60,})/g, '[REDACTED]') || 'Unknown error';
    result = { error: msg, operation };
  }

  process.stdout.write(JSON.stringify(result));
}

main().catch(err => {
  process.stdout.write(JSON.stringify({ error: err.message }));
  process.exit(1);
});
