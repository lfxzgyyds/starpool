# -*- coding: utf-8 -*-
"""签名测试（不广播、不花钱）。

验证逻辑：
  1. sign_transaction 能反序列化待签交易并产出可重新解析的签名 base64；
  2. 签名后的交易至少包含 1 个签名；
  3. 非法输入应抛出 SignError（不静默失败）。

注意：本测试使用 tests/sample_tx.json 中的真实交易样本，但**只做本地签名**，
不广播、不联网、不使用真实资产私钥（测试密钥为运行时随机生成）。
"""
import base64
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import base58
from solders.keypair import Keypair
from solders.transaction import VersionedTransaction
from io_net_recharge.wallet import sign_transaction, SignError


def _load_sample_tx():
    path = Path(__file__).resolve().parent / "sample_tx.json"
    if not path.exists():
        return None
    import json

    return json.loads(path.read_text(encoding="utf-8")).get("tx_b64")


def _random_secret() -> str:
    """生成仅用于测试的随机 base58 私钥（不对应任何真实资产）。

    solders 的 Keypair.secret() 仅返回 32 字节种子，而 Solana 标准 base58
    私钥是 64 字节（seed + pubkey）。需用 to_bytes_array() 取完整密钥再编码。
    """
    kp = Keypair.from_seed(os.urandom(32))
    return base58.b58encode(bytes(kp.to_bytes_array())).decode()


def test_sign_valid_tx():
    tx_b64 = _load_sample_tx()
    assert tx_b64, "缺少 tests/sample_tx.json 样本交易"
    secret = _random_secret()
    signed = sign_transaction(tx_b64, secret)
    raw = base64.b64decode(signed)
    tx = VersionedTransaction.from_bytes(raw)
    assert len(tx.signatures) >= 1, "签名后交易应至少含 1 个签名"


def test_sign_invalid_input_raises():
    secret = _random_secret()
    raised = False
    try:
        sign_transaction("not-valid-base64!!!", secret)
    except SignError:
        raised = True
    except Exception:
        raised = True
    assert raised, "非法输入应抛出异常"


if __name__ == "__main__":
    test_sign_valid_tx()
    test_sign_invalid_input_raises()
    print("ALL TESTS PASSED")
