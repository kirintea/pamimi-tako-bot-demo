# -*- coding: utf-8 -*-
"""渠道适配器子包

各渠道（wecom / feishu / email ...）的运行时实现放在此处的子包内，
由 core/channels/registry.py 统一注册。新增渠道：新建子包并实现
BaseChannel 子类即可，内核与 manager 无需改动。
"""
