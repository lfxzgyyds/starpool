# -*- coding: utf-8 -*-
"""命令行入口：single / batch / verify 三个子命令。

运行方式（在项目根目录）：
    python cli.py single  --refresh-cookie <cookie> --pubkey <pub> --secret <priv> [--broadcast]
    python cli.py batch   --config examples/config.example.json
    python cli.py verify  --signature <sig> --pubkey <pub>

注意：single/batch 默认只建链+签名，加 --broadcast 才真正花钱广播。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 允许从项目根目录直接运行：`python cli.py ...`
sys.path.insert(0, str(Path(__file__).resolve().parent))

from io_net_recharge.client import IoNetRechargeClient  # noqa: E402
from io_net_recharge.wallet import (  # noqa: E402
    sign_transaction,
    broadcast_transaction,
    verify_on_chain,
)
from io_net_recharge.batch import run_batch, Account  # noqa: E402

DEFAULT_RPC = "https://solana-rpc.publicnode.com"


def cmd_single(args: argparse.Namespace) -> None:
    client = IoNetRechargeClient(args.refresh_cookie, amount=args.amount)
    payload = client.build_transaction(args.pubkey)
    print(f"[*] 已建链: {payload.link_id}")

    signed = sign_transaction(payload.tx_b64, args.secret)
    if args.broadcast:
        sig = broadcast_transaction(signed, rpc=args.rpc, dry_run=args.dry_run)
        print(f"[*] 广播签名: {sig}")
        if not args.dry_run and args.verify:
            v = verify_on_chain(
                sig, args.pubkey, rpc=args.rpc, expected_usdc=float(args.amount)
            )
            print(f"[*] 链上验证: {v}")
    else:
        print("[*] 已签名（未广播）。加 --broadcast 才真正花钱。")


def cmd_batch(args: argparse.Namespace) -> None:
    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    rpc = cfg.get("rpc", DEFAULT_RPC)
    accs = [Account(**a) for a in cfg.get("accounts", [])]
    # 风控参数从 config 透传（缺省走默认值：无间隔、不阶梯冷却、不重试）。
    results = run_batch(
        accs,
        rpc=rpc,
        dry_run=args.dry_run,
        verify=args.verify,
        min_gap=float(cfg.get("min_gap", 0)),
        max_gap=float(cfg.get("max_gap", 0)),
        step_size=int(cfg.get("step_size", 0)),
        step_cooldown=float(cfg.get("step_cooldown", 0)),
        max_retries=int(cfg.get("max_retries", 1)),
        backoff_base=float(cfg.get("backoff_base", 2.0)),
    )
    for r in results:
        print(
            f"{r.name}: ok={r.ok} attempts={r.attempts} "
            f"sig={r.signature[:12]}... err={r.error}"
        )


def cmd_verify(args: argparse.Namespace) -> None:
    v = verify_on_chain(
        args.signature, args.pubkey, rpc=args.rpc, expected_usdc=float(args.amount)
    )
    print(v)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="io.net 纯 API 充值工具")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("single", help="单账号充值")
    ps.add_argument("--refresh-cookie", required=True, help="workos_refresh_token 值")
    ps.add_argument("--pubkey", required=True, help="付款钱包公钥")
    ps.add_argument("--secret", required=True, help="付款钱包私钥（base58）")
    ps.add_argument("--amount", default="10", help="充值金额 USDC，默认 10")
    ps.add_argument("--rpc", default=DEFAULT_RPC, help="Solana RPC 端点")
    ps.add_argument("--broadcast", action="store_true", help="真正广播（花钱）")
    ps.add_argument("--dry-run", action="store_true", help="构造广播但不发送")
    ps.add_argument(
        "--verify",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="广播后做链上验证",
    )
    ps.set_defaults(func=cmd_single)

    pb = sub.add_parser("batch", help="批量充值（读 config.json）")
    pb.add_argument("--config", required=True, help="账号配置文件路径")
    pb.add_argument("--dry-run", action="store_true")
    pb.add_argument(
        "--verify", action=argparse.BooleanOptionalAction, default=True
    )
    pb.set_defaults(func=cmd_batch)

    pv = sub.add_parser("verify", help="链上验证交易签名")
    pv.add_argument("--signature", required=True)
    pv.add_argument("--pubkey", required=True)
    pv.add_argument("--amount", default="10")
    pv.add_argument("--rpc", default=DEFAULT_RPC)
    pv.set_defaults(func=cmd_verify)
    return p


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
