# -*- coding: utf-8 -*-
"""批量充值编排（含风控层：抖动间隔 / 阶梯冷却 / 失败退避）。

把一个账号列表依次执行 build -> sign -> broadcast，
每个账号用各自的 refresh_cookie（io.net 登录态）与私钥（付款钱包）。

【风控设计（针对 100 账号批量场景）】
  - 抖动间隔：账号之间 sleep(min_gap + random()*(max_gap-min_gap))，
    避免固定节奏被 Cloudflare 行为风控识别为脚本。
  - 阶梯冷却：每处理 step_size 个账号，额外 sleep(step_cooldown)，
    模拟"分批次操作"而非一次性灌完。
  - 失败退避：遇到 Cloudflare 限流（key_set）/ RPC 网络抖动（BroadcastError）
    时，按 backoff_base**attempt 指数退避后重试，最多 max_retries 次；
    非瞬态错误（如私钥错误 SignError、建链失败 TopupError）不重试，直接判失败。
  - 链上验证不重试（设计决策）：广播一旦返回签名即视为已上链尝试，
    重放同一笔已签交易可能造成双花，故 verify_on_chain 的失败（含确认查询的网络抖动）
    一律判账号失败、不重试，由人工核查链上状态。
  - 已知局限：IP 层未做代理轮换（curl_cffi 已预留 proxies= 能力但未接入），
    100 账号共用单一出口 IP 仍是高风险点，详见 README/API 风控章节。

注意：本文件只新增节奏与重试逻辑，**未改动签名/广播主链路**
（sign_transaction / broadcast_transaction / verify_on_chain 来自 wallet.py）。
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import List, Tuple, Type

from .client import IoNetRechargeClient
from .wallet import sign_transaction, broadcast_transaction, verify_on_chain
from .errors import KeySetBlockedError, BroadcastError, IonetRechargeError

DEFAULT_RPC = "https://solana-rpc.publicnode.com"

# 视为"瞬态、可重试"的异常：Cloudflare 限流 + 广播网络抖动。
# 私钥/交易格式类错误（SignError）不在内，重试无意义。
# 注意：verify_on_chain 的错误（含链上确认查询的网络抖动）刻意不在此列——
# 因为广播一旦返回签名即视为已发出，重试广播存在双花风险。
RETRYABLE: Tuple[Type[Exception], ...] = (KeySetBlockedError, BroadcastError)


@dataclass
class Account:
    """单个充值账号的配置。"""

    name: str
    refresh_cookie: str
    secret_base58: str
    pubkey: str
    amount: str = "10"


@dataclass
class BatchResult:
    """单个账号的批量执行结果。"""

    name: str
    link_id: str = ""
    signature: str = ""
    ok: bool = False
    error: str = ""
    attempts: int = 1  # 实际尝试次数（含重试）


def _jitter_gap(min_gap: float, max_gap: float) -> float:
    """在 [min_gap, max_gap] 区间内生成带随机抖动的间隔（秒）。

    若 max_gap <= min_gap，直接返回 max(min_gap, 0)，不做随机。
    """
    if max_gap <= min_gap:
        return max(min_gap, 0.0)
    return random.uniform(min_gap, max_gap)


def run_batch(
    accounts: List[Account],
    rpc: str = DEFAULT_RPC,
    dry_run: bool = False,
    verify: bool = True,
    min_gap: float = 0.0,
    max_gap: float = 0.0,
    step_size: int = 0,
    step_cooldown: float = 0.0,
    max_retries: int = 1,
    backoff_base: float = 2.0,
) -> List[BatchResult]:
    """对多个账号执行 build -> sign -> broadcast，内置风控节奏。

    Args:
        accounts: Account 列表，每个元素代表一个 io.net 账号 + 付款钱包。
        rpc: Solana RPC 端点。
        dry_run: True 时不广播（不花钱），仅验证 build+sign 链路。
        verify: 广播后是否做链上验证（USDC 扣减确认）。
        min_gap / max_gap: 账号间最小/最大间隔（秒），实际间隔带随机抖动。
        step_size: 每处理 N 个账号插入一次阶梯冷却；0 表示不启用。
        step_cooldown: 阶梯冷却时长（秒）。
        max_retries: 瞬态失败（限流/网络）最大重试次数（不含首次）。
        backoff_base: 指数退避底数，第 k 次重试前等待 backoff_base**k 秒。

    Returns:
        BatchResult 列表，与输入顺序一致。单个账号失败不影响其他账号。
    """
    results: List[BatchResult] = []
    total = len(accounts)

    for idx, acc in enumerate(accounts):
        # 首个账号前不插入间隔；其余账号前插入抖动间隔，避免固定节奏。
        if idx > 0:
            gap = _jitter_gap(min_gap, max_gap)
            if gap > 0:
                time.sleep(gap)

        res = BatchResult(name=acc.name)
        res.ok = _process_one(
            acc, res, rpc, dry_run, verify, max_retries, backoff_base
        )
        results.append(res)

        # 阶梯冷却：每 step_size 个账号后额外冷却，模拟分批次操作。
        # 末批之后不再冷却（已无后续账号）。
        if step_size > 0 and (idx + 1) % step_size == 0 and (idx + 1) < total:
            if step_cooldown > 0:
                time.sleep(step_cooldown)

    return results


def _process_one(
    acc: Account,
    res: BatchResult,
    rpc: str,
    dry_run: bool,
    verify: bool,
    max_retries: int,
    backoff_base: float,
) -> bool:
    """处理单个账号，带指数退避重试。返回是否成功。

    重试只针对 RETRYABLE（Cloudflare 限流 / 广播网络抖动）；
    其余异常（含私钥错误等非瞬态）直接判失败，不浪费重试额度。
    """
    attempt = 0
    max_attempts = max_retries + 1  # 首次 + max_retries 次重试

    while True:
        try:
            # 每次尝试重建 client，重新 refresh token，利于限流后恢复。
            client = IoNetRechargeClient(acc.refresh_cookie, amount=acc.amount)
            payload = client.build_transaction(acc.pubkey)
            res.link_id = payload.link_id

            signed = sign_transaction(payload.tx_b64, acc.secret_base58)
            sig = broadcast_transaction(signed, rpc=rpc, dry_run=dry_run)
            res.signature = sig

            if dry_run:
                res.attempts = attempt + 1
                return True
            # 链上验证：广播已发生（sig 已返回），此处仅确认扣款是否到账。
            # 该步骤的异常走 IonetRechargeError 分支，不会被 RETRYABLE 捕获重试——
            # 因为重复广播同一笔已签交易可能造成双花。验证失败即判该账号失败，需人工核查。
            if verify:
                v = verify_on_chain(
                    sig, acc.pubkey, rpc=rpc, expected_usdc=float(acc.amount)
                )
                res.attempts = attempt + 1
                if not v["ok"]:
                    res.error = str(v.get("err") or v)
                    return False
                return True
            res.attempts = attempt + 1
            return bool(sig)

        except RETRYABLE as exc:
            attempt += 1
            res.attempts = attempt
            if attempt >= max_attempts:
                res.error = f"重试 {max_retries} 次仍失败: {exc}"
                return False
            time.sleep(backoff_base ** attempt)

        except IonetRechargeError as exc:
            # 非瞬态业务错误（SignError / TopupError / PaymentLinkError 等）：
            # 重试无意义，直接记录失败。
            res.attempts = attempt + 1
            res.error = str(exc)
            return False
