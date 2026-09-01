# -*- coding: utf-8 -*-
"""数据结构定义。"""
from dataclasses import dataclass


@dataclass
class TransactionPayload:
    """一笔充值交易的载体，贯穿 build -> sign -> broadcast 全流程。

    Attributes:
        link_id:   SpherePay payment link id，形如 paymentLink_xxxxxxxx。
        link_url:  完整的 payment link URL。
        tx_b64:    未签名的 VersionedTransaction（base64 编码）。
        pub:       付款钱包公钥（即 link 创建时传入的 account）。
        signed_b64: 签名后的交易（base64），由 sign_transaction 填充。
        signature:  广播后在链上的交易签名，由 broadcast_transaction 填充。
    """

    link_id: str
    link_url: str
    tx_b64: str
    pub: str
    signed_b64: str = ""
    signature: str = ""
