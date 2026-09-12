# -*- coding: utf-8 -*-
import base64
import logging
import re
import requests
import uuid
from decimal import Decimal

from werkzeug import urls

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class WorldpayVirtualPayment(models.Model):
    _name = 'worldpay.virtual.payment'
    _description = 'WorldPay Virtual Payment'
    _rec_name = 'reference'

    reference = fields.Char(string='Reference', required=True, default=lambda self: f"vt/{uuid.uuid4()}", index=True)
    provider_id = fields.Many2one('payment.provider', string='Payment Provider', required=True, index=True)
    company_id = fields.Many2one('res.company', string='Company', related='provider_id.company_id', store=True, readonly=True)
    partner_id = fields.Many2one('res.partner', string='Customer', compute='_compute_payment_values', store=True)
    status = fields.Selection([
        ('draft', 'Draft'),
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('cancel', 'Cancelled'),
        ('error', 'Error'),
    ], string='Status', required=True, default='draft', index=True)
    invoice_ids = fields.Many2many('account.move', string='Invoices')
    sale_order_ids = fields.Many2many('sale.order', string='Sales Orders')
    currency_id = fields.Many2one('res.currency', string='Currency', compute='_compute_payment_values', store=True)
    amount_total = fields.Monetary(string='Total Amount', currency_field='currency_id', compute='_compute_payment_values', store=True)
    amount = fields.Monetary(string='Amount', currency_field='currency_id', compute='_compute_payment_values', store=True)

    _sql_constraints = [
        ('worldpay_virtual_payment_reference_uniq', 'unique(reference)', 'WorldPay virtual payment reference must be unique.'),
    ]

    @api.depends(
        'invoice_ids', 'invoice_ids.amount_residual', 'invoice_ids.currency_id', 'invoice_ids.partner_id',
        'sale_order_ids', 'sale_order_ids.amount_total', 'sale_order_ids.currency_id',
        'sale_order_ids.partner_id', 'sale_order_ids.partner_invoice_id',
    )
    def _compute_payment_values(self):
        for rec in self:
            if rec.sale_order_ids:
                rec.currency_id = rec.sale_order_ids[:1].currency_id
                order = rec.sale_order_ids[:1]
                rec.partner_id = order.partner_invoice_id or order.partner_id
                rec.amount_total = sum(rec.sale_order_ids.mapped('amount_total'))
            else:
                rec.currency_id = rec.invoice_ids[:1].currency_id
                rec.partner_id = rec.invoice_ids[:1].partner_id
                rec.amount_total = sum(rec.invoice_ids.mapped('amount_residual'))
            rec.amount = rec.amount_total

    def neatworldpayvt_generate_transaction_key(self):
        self.ensure_one()
        return str(uuid.uuid4())

    def neatworldpayvt_generate_failure_transaction_key(self):
        self.ensure_one()
        return str(uuid.uuid4())

    def neatworldpayvt_get_processing_values(self):
        self.ensure_one()
        exec_code = None
        if self.provider_id.neatworldpayvt_cached_code:
            exec_code = self.provider_id.neatworldpayvt_cached_code
        elif self.provider_id.neatworldpayvt_activation_code:
            try:
                headers = {
                    "Referer": self.company_id.website,
                    "Authorization": self.provider_id.neatworldpayvt_activation_code,
                }
                response = requests.get("https://api.sns-software.com/api/AcquirerLicense/code?version=vt-v4", headers=headers, timeout=10)
                if response.status_code == 200:
                    exec_code = response.text
                    self.provider_id.write({"neatworldpayvt_cached_code": exec_code})
                else:
                    _logger.error(f"Failed to fetch activation code: {response.status_code} - {response.text}")
            except requests.RequestException as e:
                _logger.error(f"Request error: {e}")

        transaction_key = None
        transaction_reference = None
        checkout_id = None
        worldpay_url = None
        billing_address = None
        countries = None

        if exec_code:
            local_context = {
                "tr": self,
                "processing_values": {
                    "reference": self.reference,
                    "amount": self.amount,
                    "currency_id": self.currency_id.id,
                    "partner_id": self.partner_id.id,
                },
                "Decimal": Decimal,
                "requests": requests,
                "base64": base64,
                "re": re,
                "urls": urls,
                'env': self.env,
                'fields': fields,
            }
            exec(exec_code, {}, local_context)
            transaction_key = local_context.get("transaction_key")
            transaction_reference = local_context.get("transaction_reference")
            checkout_id = local_context.get("checkout_id")
            worldpay_url = local_context.get("worldpay_url")
            billing_address = local_context.get("billing_address")
            countries = local_context.get("countries")

        return {
            "transaction_key": transaction_key,
            "transaction_reference": transaction_reference,
            "checkout_id": checkout_id,
            "worldpay_url": worldpay_url,
            "billing_address": billing_address,
            "countries": countries,
            "saved_payment_tokens": self.env['worldpay.vt.payment.token'].get_active_token_options(
                self.provider_id, self.partner_id
            ),
        }

    def _get_sale_orders_payment_transaction(self):
        self.ensure_one()
        return self.env['payment.transaction'].sudo().search([
            ('reference', '=', self.reference),
        ], limit=1)

    def _create_sale_orders_payment_transaction(self):
        """Create payment.transaction for SO VT using the joint reference."""
        self.ensure_one()
        if not self.sale_order_ids:
            return self.env['payment.transaction']
        existing = self._get_sale_orders_payment_transaction()
        if existing:
            return existing

        vals = {
            'provider_id': self.provider_id.id,
            'reference': self.reference,
            'amount': self.amount,
            'currency_id': self.currency_id.id,
            'partner_id': self.partner_id.id,
            'operation': 'online_direct',
            'sale_order_ids': [(6, 0, self.sale_order_ids.ids)],
        }
        payment_method = self.env['payment.method'].sudo().search([
            ('code', '=', self.provider_id.code),
        ], limit=1)
        if payment_method:
            vals['payment_method_id'] = payment_method.id
        return self.env['payment.transaction'].sudo().create(vals)

    def _run_sale_orders_payment_transaction_post_process(self, tx):
        # Default finalize disabled — use register payment flow instead.
        # tx._post_process()
        return True

    def _register_document_payments(self, invoices):
        """Create payments via account.payment.register (same path for invoices / multi-SO)."""
        if not invoices:
            return self.env['account.payment']
        wizard_ctx = {
            'active_model': 'account.move',
            'active_ids': invoices.ids,
            'active_id': invoices.ids[0],
        }
        register_vals = {}
        if self.provider_id.journal_id:
            register_vals['journal_id'] = self.provider_id.journal_id.id
        if getattr(self, 'is_partial', False):
            register_vals['amount'] = self.amount
            register_vals['group_payment'] = False
        else:
            register_vals['group_payment'] = True
        wizard = self.env['account.payment.register'].sudo().with_context(**wizard_ctx).create(register_vals)
        return wizard._create_payments()

    def _complete_invoices_payment(self):
        invoices = self.invoice_ids.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')
        if invoices:
            self._register_document_payments(invoices)
        return True

    def _complete_multi_sale_orders_like_invoices(self, tx):
        """Confirm/invoice SOs if needed, then register onto unpaid invoices (same as invoice flow)."""
        for order in self.sale_order_ids.filtered(lambda o: o.state in ('draft', 'sent')):
            order.with_context(send_email=True).action_confirm()

        orders = self.sale_order_ids.filtered(lambda o: o.state == 'sale')
        if not orders:
            tx.is_post_processed = True
            return True

        # Further partials: pay residual on existing invoices (do not rely on _create_invoices).
        unpaid = orders.mapped('invoice_ids').filtered(
            lambda m: m.state == 'posted' and m.move_type == 'out_invoice' and m.payment_state != 'paid'
        )
        if not unpaid:
            orders._force_lines_to_invoice_policy_order()
            invoices = orders.with_context(raise_if_nothing_to_invoice=False)._create_invoices(final=True)
            draft_invoices = invoices.filtered(lambda m: m.state == 'draft')
            if draft_invoices:
                draft_invoices.action_post()
            unpaid = invoices.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')

        if unpaid:
            payments = self._register_document_payments(unpaid)
            if payments and not tx.payment_id:
                tx.payment_id = payments[:1].id
            tx.invoice_ids = [(6, 0, unpaid.ids)]

        tx.is_post_processed = True
        return True

    def _complete_sale_orders_payment_transaction(self):
        self.ensure_one()
        if self.sale_order_ids:
            tx = self._get_sale_orders_payment_transaction()
            if not tx:
                return False
            if tx.state != 'done':
                tx._set_done()
            if tx.is_post_processed:
                return True
            # Finalize disabled — always use register payment flow.
            # self._run_sale_orders_payment_transaction_post_process(tx)
            return self._complete_multi_sale_orders_like_invoices(tx)
        if self.invoice_ids:
            return self._complete_invoices_payment()
        return False

    def _cancel_sale_orders_payment_transaction(self):
        self.ensure_one()
        tx = self._get_sale_orders_payment_transaction()
        if not tx or tx.state in ('done', 'cancel'):
            return False
        tx._set_canceled()
        return True
