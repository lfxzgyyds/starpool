# -*- coding: utf-8 -*-
"""io-net-api-recharge

纯 API 的 io.net Compute Credits 充值库。

设计目标：
- 不依赖浏览器点击，全部走 HTTP API（Cloudflare TLS 指纹用 curl_cffi 伪装）；
- 私钥仅在本地做离线签名，绝不外传；
- 单账号与批量账号均可编排。

典型用法::

    from io_net_recharge import IoNetRechargeClient, sign_transaction, broadcast_transaction

    client = IoNetRechargeClient(refresh_cookie="<workos_refresh_token>")
    payload = client.build_transaction(pubkey="<你的钱包公钥>")
    signed = sign_transaction(payload.tx_b64, secret_base58="<你的私钥>")
    sig = broadcast_transaction(signed)   # 这一步才真正花钱
"""

from .client import IoNetRechargeClient, TransactionPayload
from .wallet import sign_transaction, broadcast_transaction, verify_on_chain
from .batch import run_batch, Account, BatchResult
from .errors import (
    IonetRechargeError,
    AuthError,
    TopupError,
    PaymentLinkError,
    KeySetBlockedError,
    SignError,
    BroadcastError,
)

__all__ = [
    "IoNetRechargeClient",
    "TransactionPayload",
    "sign_transaction",
    "broadcast_transaction",
    "verify_on_chain",
    "run_batch",
    "Account",
    "BatchResult",
    "IonetRechargeError",
    "AuthError",
    "TopupError",
    "PaymentLinkError",
    "KeySetBlockedError",
    "SignError",
    "BroadcastError",
]
