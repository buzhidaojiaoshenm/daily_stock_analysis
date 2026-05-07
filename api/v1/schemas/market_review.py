# -*- coding: utf-8 -*-
"""Market review API schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class MarketReviewRunRequest(BaseModel):
    region: Literal["cn", "us", "both"] = "cn"
    send_notification: bool = False


class MarketReviewRunResponse(BaseModel):
    status: Literal["completed"]
    region: Literal["cn", "us", "both"]
    report: str
    notification_sent: bool

