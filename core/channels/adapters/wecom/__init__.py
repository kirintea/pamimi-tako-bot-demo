# -*- coding: utf-8 -*-
"""WeCom 渠道适配器包"""

from core.channels.adapters.wecom.config import WecomSettings
from core.channels.adapters.wecom.manifest import WECOM_MANIFEST
from core.channels.adapters.wecom.runtime import WecomChannel

__all__ = ["WecomChannel", "WecomSettings", "WECOM_MANIFEST"]
