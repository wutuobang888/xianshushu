# -*- coding: utf-8 -*-
"""金额边界：验证后转整数分，未知金额保留为空。"""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


def to_fen(value, allow_none=False):
    if value in (None, "") and allow_none:
        return None
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            raise ValueError
        return int(amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        raise ValueError("金额必须是有限的非负数") from None
