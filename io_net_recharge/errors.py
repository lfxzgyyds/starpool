# -*- coding: utf-8 -*-
"""自定义异常层级。

所有异常都继承自 IonetRechargeError，便于调用方统一捕获。
"""


class IonetRechargeError(Exception):
    """充值流程的基础异常。"""


class AuthError(IonetRechargeError):
    """刷新 / 获取 accessToken 失败。"""


class TopupError(IonetRechargeError):
    """创建充值订单（top-up-credits）失败。"""


class PaymentLinkError(IonetRechargeError):
    """获取待签交易（SpherePay pay）失败。"""


class KeySetBlockedError(IonetRechargeError):
    """被 Cloudflare Bot Management 以 {"key_set": true} 软拦截。

    出现此错误几乎都是因为请求缺少 Chrome 的 TLS 指纹伪装
    （必须使用 curl_cffi 的 impersonate="chrome"）。详见 API.md。
    """


class SignError(IonetRechargeError):
    """交易离线签名失败（私钥 / 交易格式问题）。"""


class BroadcastError(IonetRechargeError):
    """广播交易到 Solana 失败。"""
