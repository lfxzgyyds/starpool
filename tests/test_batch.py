# -*- coding: utf-8 -*-
"""风控层单元测试（不联网）。

验证：
  1. _jitter_gap 落在 [min_gap, max_gap] 区间内；
  2. 瞬态失败（KeySetBlockedError）触发重试，最终成功 -> ok=True 且 attempts 正确；
  3. 重试耗尽 -> ok=False，且 attempts = max_retries + 1。

通过 unittest.mock 替换网络调用，避免真实请求。
"""
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from io_net_recharge import batch as batch_mod
from io_net_recharge.batch import run_batch, Account, _jitter_gap
from io_net_recharge.errors import KeySetBlockedError


def test_jitter_gap_range():
    for _ in range(300):
        g = _jitter_gap(8, 25)
        assert 8.0 <= g <= 25.0, f"jitter out of range: {g}"
    # 退化情况：max_gap <= min_gap 时直接返回 min_gap
    assert _jitter_gap(10, 10) == 10.0
    assert _jitter_gap(0, 0) == 0.0


def test_retry_then_success():
    acc = Account(name="a", refresh_cookie="x", secret_base58="y", pubkey="z")
    state = {"n": 0}

    def fake_build(self, pubkey):
        return mock.MagicMock(link_id="L1")

    def fake_sign(tx, secret):
        return "signed"

    def fake_broadcast(signed, rpc=None, dry_run=False):
        state["n"] += 1
        if state["n"] <= 2:  # 前两次模拟 Cloudflare 限流
            raise KeySetBlockedError("throttled")
        return "sig123"

    with mock.patch.object(batch_mod.IoNetRechargeClient, "build_transaction", fake_build), \
         mock.patch.object(batch_mod, "sign_transaction", fake_sign), \
         mock.patch.object(batch_mod, "broadcast_transaction", fake_broadcast):
        # verify=False 避免触达真实 RPC 网络，保持离线单测。
        res = run_batch([acc], max_retries=3, backoff_base=1.0,
                        min_gap=0, max_gap=0, verify=False)

    assert res[0].ok is True, "重试后成功应 ok=True"
    assert res[0].attempts == 3, f"attempts 应为 3，实际 {res[0].attempts}"
    assert res[0].signature == "sig123"


def test_retry_exhausted():
    acc = Account(name="a", refresh_cookie="x", secret_base58="y", pubkey="z")

    def fake_build(self, pubkey):
        raise KeySetBlockedError("always throttled")

    with mock.patch.object(batch_mod.IoNetRechargeClient, "build_transaction", fake_build):
        res = run_batch([acc], max_retries=2, backoff_base=1.0)

    assert res[0].ok is False, "重试耗尽应 ok=False"
    assert res[0].attempts == 3, f"attempts 应为 3（2 次重试+1 首次），实际 {res[0].attempts}"
    assert "重试" in res[0].error


if __name__ == "__main__":
    test_jitter_gap_range()
    test_retry_then_success()
    test_retry_exhausted()
    print("ALL BATCH TESTS PASSED")
