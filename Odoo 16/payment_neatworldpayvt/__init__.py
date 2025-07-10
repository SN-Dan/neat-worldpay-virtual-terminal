# -*- coding: utf-8 -*-
# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.

from . import models
from . import controllers

from odoo.addons.payment import setup_provider, reset_payment_provider


def post_init_hook(cr, registry):
    setup_provider(cr, registry, 'neatworldpayvt')


def uninstall_hook(cr, registry):
    reset_payment_provider(cr, registry, 'neatworldpayvt')