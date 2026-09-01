# -*- coding: utf-8 -*-
"""Solana 钱包签名与广播（基于 solders 底层实现）。

签名链路：
    Keypair.from_base58_string  ->  恢复私钥
    VersionedTransaction.from_bytes  ->  反序列化待签交易
    Keypair.sign_message(bytes(message))  ->  对编译后 message 做 ed25519 签名
    VersionedTransaction.populate(message, [sig])  ->  重建带签名交易

广播使用 curl_cffi 直连 Solana RPC 的 sendTransaction（避免高层 client 版本差异）。
支持 dry_run，便于在不花钱的情况下测试整条链路。
"""
from __future__ import annotations

import base64
import time

from curl_cffi import requests
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction

from .errors import SignError, BroadcastError

# Solana 主网 USDC mint 地址
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
DEFAULT_RPC = "https://solana-rpc.publicnode.com"


def sign_transaction(tx_b64: str, secret_base58: str) -> str:
    """用 base58 私钥离线签名 v0 交易。

    Args:
        tx_b64: 未签名的 VersionedTransaction（base64 编码）。
        secret_base58: 64 字节私钥的 base58 编码字符串。

    Returns:
        签名后交易的 base64 编码。

    Raises:
        SignError: 密钥或交易格式错误导致签名失败。
    """
    try:
        keypair = Keypair.from_base58_string(secret_base58)
        tx = VersionedTransaction.from_bytes(base64.b64decode(tx_b64))
        # Solana 交易签名 = 对编译后的 message 做 ed25519 签名
        sig = keypair.sign_message(bytes(tx.message))
        # 用签名重建带签名的交易。io.net 充值交易仅 feePayer 一个签名者，
        # 故签名列表长度为 1；若未来遇到多签名者交易需相应扩展。
        signed = VersionedTransaction.populate(tx.message, [sig])
        return base64.b64encode(bytes(signed)).decode()
    except SignError:
        raise
    except Exception as exc:  # noqa: BLE001 - 统一封装为 SignError
        raise SignError(f"签名失败: {exc}") from exc


def broadcast_transaction(
    signed_b64: str,
    rpc: str = DEFAULT_RPC,
    dry_run: bool = False,
    skip_preflight: bool = True,
) -> str:
    """广播已签名交易到 Solana。

    Args:
        signed_b64: 签名后交易的 base64 编码。
        rpc: Solana RPC 端点。
        dry_run: True 时只构造请求、不真实发送（用于测试，不花钱）。
        skip_preflight: 是否跳过预检（默认 True，与原始链路一致）。

    Returns:
        链上交易签名；dry_run 时返回占位字符串 "dry_run_no_broadcast"。

    Raises:
        BroadcastError: RPC 返回错误或网络异常。
    """
    if dry_run:
        return "dry_run_no_broadcast"

    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "sendTransaction",
        "params": [signed_b64, {"encoding": "base64", "skipPreflight": skip_preflight}],
    }
    try:
        session = requests.Session()
        r = session.post(rpc, json=body, timeout=30)
        payload = r.json()
        if payload.get("error"):
            raise BroadcastError(f"sendTransaction 错误: {payload['error']}")
        return payload.get("result")
    except BroadcastError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise BroadcastError(f"广播失败: {exc}") from exc


def verify_on_chain(
    signature: str,
    payer_pub: str,
    rpc: str = DEFAULT_RPC,
    expected_usdc: float = 10.0,
    retries: int = 10,
) -> dict:
    """链上确认：交易成功且付款方 USDC 精确减少 expected_usdc。

    Args:
        signature: 广播后得到的链上交易签名。
        payer_pub: 付款钱包公钥（用于定位其 USDC 变动）。
        rpc: Solana RPC 端点。
        expected_usdc: 预期扣减的 USDC 数量。
        retries: 轮询交易确认的尝试次数。

    Returns:
        dict: {"ok": bool, "err": Any, "usdc_delta": float}
        usdc_delta 为负值（付款方视角）。
    """
    session = requests.Session()
    tx = None
    for _ in range(retries):
        r = session.post(
            rpc,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getTransaction",
                "params": [
                    signature,
                    {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0},
                ],
            },
            timeout=30,
        )
        tx = (r.json() or {}).get("result")
        if tx:
            break
        time.sleep(3)

    if not tx:
        return {"ok": False, "err": "交易未在链上找到", "usdc_delta": None}

    meta = tx.get("meta", {})
    if meta.get("err"):
        return {"ok": False, "err": meta["err"], "usdc_delta": None}

    delta = 0.0
    for tb in meta.get("preTokenBalances", []):
        if tb.get("mint") == USDC_MINT and tb.get("owner") == payer_pub:
            idx = tb.get("accountIndex")
            pre = float(tb.get("uiTokenAmount", {}).get("uiAmount") or 0)
            post = pre
            for pb in meta.get("postTokenBalances", []):
                if pb.get("accountIndex") == idx:
                    post = float(pb.get("uiTokenAmount", {}).get("uiAmount") or 0)
                    break
            delta = post - pre  # 付款方应为负
            break

    ok = abs(abs(delta) - expected_usdc) < 0.01
    return {"ok": ok, "err": None, "usdc_delta": delta}
