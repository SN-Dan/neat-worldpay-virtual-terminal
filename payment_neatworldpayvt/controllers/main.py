# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.
# This module extends Odoo's payment framework.
# Odoo is a trademark of Odoo S.A.

import base64
import json
import logging
import re
import requests
import time
import uuid
from decimal import Decimal
from odoo.http import request
from odoo import _, http, fields
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class NeatWorldpayVTController(http.Controller):

    _allowed_ips = [
        '34.246.73.11', '52.215.22.123', '52.31.61.0', '18.130.125.132',
        '35.176.91.145', '52.56.235.128', '18.185.7.67', '18.185.134.117',
        '18.185.158.215', '52.48.6.187', '34.243.65.63', '3.255.13.18',
        '3.251.36.74', '63.32.208.6', '52.19.45.138', '3.11.50.124',
        '3.11.213.43', '3.14.190.43', '3.121.172.32', '3.125.11.252',
        '3.126.98.120', '3.139.153.185', '3.139.255.63', '13.200.51.10',
        '13.200.56.25', '13.232.151.127', '34.236.63.10', '34.253.172.98',
        '35.170.209.108', '35.177.246.6', '52.4.68.25', '52.51.12.88',
        '108.129.30.203'
    ]

    def _is_guid_reference(self, reference):
        if (reference or '').startswith('vt/'):
            return True
        try:
            uuid.UUID(reference or "")
            return True
        except Exception:
            return False

    def _is_payment_link_reference(self, reference):
        return (reference or '').startswith('pl/')

    def _schedule_multi_invoice_failure_activity(self, invoices, reference, fallback_user_id=False):
        for invoice in invoices:
            user_id = invoice.user_id.id if invoice.user_id else (int(fallback_user_id) if fallback_user_id else None)
            invoice.activity_schedule(
                act_type_xmlid='mail.mail_activity_data_todo',
                user_id=user_id,
                date_deadline=fields.Date.today(),
                summary="Payment Failed - Action Required",
                note=f"The payment failed after initial confirmation {reference}. Please review and take action."
            )
            _logger.info(f"\n Invoice Found for cancelled transaction creating activity {reference} {invoice} \n")

    def _handle_virtual_payment(self, payment, result_state):
        if not payment:
            return False
        target_status = 'paid' if result_state == 'done' else result_state
        if payment.status == 'paid' and result_state in ('cancel', 'error'):
            return True
        if payment.status == target_status:
            return True
        if payment.status == 'paid' and target_status in ('pending', 'cancel', 'error'):
            return True
        if result_state in ('pending', 'cancel', 'error'):
            payment.sudo().write({'status': result_state})
        invoices = payment.invoice_ids.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')
        invoice_names = ', '.join(payment.invoice_ids.mapped('name'))
        if result_state == 'done' and invoices:
            wizard_ctx = {
                'active_model': 'account.move',
                'active_ids': invoices.ids,
                'active_id': invoices.ids[0],
            }
            register_wizard_vals = {}
            if payment.provider_id.journal_id:
                register_wizard_vals['journal_id'] = payment.provider_id.journal_id.id
            register_wizard_vals['group_payment'] = True
            register_wizard = request.env['account.payment.register'].sudo().with_context(**wizard_ctx).create(register_wizard_vals)
            register_wizard._create_payments()

            note_body = (
                f"Payment was made for reference {payment.reference}. "
                f"Multiple invoices were paid together. "
                f"Invoices in this virtual terminal payment: {invoice_names}"
            )
            admin_user = request.env.ref('base.user_admin')
            for invoice in payment.invoice_ids:
                invoice.with_user(admin_user).sudo().message_post(
                    body=note_body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            payment.sudo().write({'status': 'paid'})
        elif result_state == 'done':
            payment.sudo().write({'status': 'paid'})
        return True

    def _handle_payment_link_invoices(self, reference, result_state):
        try:
            link_rec = request.env['worldpay.payment.link'].sudo().search([('reference', '=', reference)], limit=1)
        except Exception:
            return False
        if not link_rec:
            return False
        target_status = 'paid' if result_state == 'done' else result_state
        if link_rec.status == 'paid' and result_state in ('cancel', 'error'):
            return True
        if link_rec.status == target_status:
            return True
        if link_rec.status == 'paid' and target_status in ('pending', 'cancel', 'error'):
            return True

        if result_state in ('pending', 'cancel', 'error'):
            link_rec.sudo().write({'status': result_state})

        invoices = link_rec.invoice_ids.filtered(lambda m: m.state == 'posted' and m.payment_state != 'paid')
        invoice_names = ', '.join(link_rec.invoice_ids.mapped('name'))
        if result_state == 'done' and invoices:
            wizard_ctx = {
                'active_model': 'account.move',
                'active_ids': invoices.ids,
                'active_id': invoices.ids[0],
            }
            register_wizard_vals = {}
            if link_rec.provider_id.journal_id:
                register_wizard_vals['journal_id'] = link_rec.provider_id.journal_id.id
            register_wizard_vals['group_payment'] = True
            register_wizard = request.env['account.payment.register'].sudo().with_context(**wizard_ctx).create(register_wizard_vals)
            register_wizard._create_payments()

            note_body = (
                f"Payment was made for reference {reference}. "
                f"Multiple invoices were paid together. "
                f"Invoices in this payment link: {invoice_names}"
            )
            admin_user = request.env.ref('base.user_admin')
            for invoice in link_rec.invoice_ids:
                invoice.with_user(admin_user).sudo().message_post(
                    body=note_body,
                    message_type='comment',
                    subtype_xmlid='mail.mt_note',
                )
            link_rec.sudo().write({'status': 'paid'})
        elif result_state == 'done':
            link_rec.sudo().write({'status': 'paid'})
        return True

    @http.route('/neatworldpayvt/invoice_payment/<int:wizard_id>', type='http', auth='user', website=True)
    def neatworldpayvt_invoice_payment_page(self, wizard_id, **kwargs):
        wizard = request.env['worldpay.vt.popup'].sudo().browse(wizard_id).exists()
        if not wizard:
            return request.not_found()
        vt_providers = request.env['payment.provider'].sudo().search([
            ('code', '=', 'neatworldpayvt'),
            ('state', '!=', 'disabled'),
        ])
        return request.render('payment_neatworldpayvt.worldpay_vt_invoice_payment_page', {
            'wizard': wizard,
            'vt_providers': vt_providers,
        })

    def _neatworldpayvt_invoice_provider_for_wizard(self, wizard, provider_id):
        provider = request.env['payment.provider'].sudo().browse(int(provider_id)).exists()
        if not provider or provider.code != 'neatworldpayvt' or provider.state == 'disabled':
            return request.env['payment.provider']
        if not wizard.virtual_payment_id:
            return request.env['payment.provider']
        return provider

    @http.route(
        '/neatworldpayvt/invoice_payment/<int:wizard_id>/checkout',
        type='http',
        auth='user',
        website=True,
        methods=['POST'],
        csrf=False,
    )
    def neatworldpayvt_invoice_payment_checkout(self, wizard_id, **kwargs):
        wizard = request.env['worldpay.vt.popup'].sudo().browse(wizard_id).exists()
        if not wizard:
            return request.make_json_response({'ok': False, 'error': 'not_found'}, status=404)
        payload = request.get_json_data() or {}
        provider_id = payload.get('provider_id')
        if not provider_id:
            return request.make_json_response({'ok': False, 'error': 'missing_provider'}, status=400)
        provider = self._neatworldpayvt_invoice_provider_for_wizard(wizard, provider_id)
        if not provider:
            return request.make_json_response({'ok': False, 'error': 'invalid_provider'}, status=400)
        wizard.virtual_payment_id.sudo().write({'provider_id': provider.id})
        processing_values = wizard.virtual_payment_id.neatworldpayvt_get_processing_values()
        wizard.sudo().write({
            'provider_id': provider.id,
            'transaction_reference': processing_values.get('transaction_reference'),
            'transaction_key': processing_values.get('transaction_key'),
            'checkout_id': processing_values.get('checkout_id'),
            'worldpay_url': processing_values.get('worldpay_url'),
            'billing_address_json': json.dumps(processing_values.get('billing_address') or {}),
            'countries_json': json.dumps(processing_values.get('countries') or []),
        })
        return request.make_json_response({
            'ok': True,
            'transaction_reference': wizard.transaction_reference,
            'transaction_key': wizard.transaction_key,
            'checkout_id': wizard.checkout_id,
            'worldpay_url': wizard.worldpay_url,
            'billing_address': processing_values.get('billing_address') or {},
            'countries': processing_values.get('countries') or [],
            'provider_id': provider.id,
        })

    @http.route('/neatworldpayvt/invoice_payment/<int:wizard_id>/pay', type='http', auth='user', website=True)
    def neatworldpayvt_invoice_payment_pay_page(self, wizard_id, **kwargs):
        wizard = request.env['worldpay.vt.popup'].sudo().browse(wizard_id).exists()
        if not wizard:
            return request.not_found()
        return request.render('payment_neatworldpayvt.worldpay_vt_invoice_payment_checkout', {
            'transaction_reference': wizard.transaction_reference,
            'transaction_key': wizard.transaction_key,
            'checkout_id': wizard.checkout_id,
            'worldpay_url': wizard.worldpay_url,
            'billing_address_json': wizard.billing_address_json,
            'countries_json': wizard.countries_json,
            'provider_id': wizard.provider_id.id,
            'wizard_id': wizard.id,
        })


    @http.route(
        "/neatworldpayvt/wh", type="http", auth="public", csrf=False, methods=["POST", "GET"]
    )
    def neatworldpayvt_wh(self, **kwargs):
        client_ip = request.httprequest.remote_addr
        _logger.info(f"\n Client IP {client_ip} \n")
        if client_ip not in self._allowed_ips:
            return request.make_json_response({
                'error': 'Forbidden',
                'message': 'Forbidden'
            }, status=403)

        response = request.get_json_data()
        _logger.info(f"\n WH Response {response} \n")
        try:
            event_details = response.get("eventDetails") if response else False
            if not event_details:
                return request.make_json_response({
                    'error': 'Bad Request',
                    'message': 'Bad Request'
                }, status=400)

            transaction_reference = event_details.get("transactionReference", False)
            wp_state = event_details.get("type", False)
            result_state = 'error'
            if wp_state == "sentForAuthorization":
                result_state = 'pending'
            elif wp_state == "authorized":
                result_state = "done"
            elif wp_state == "cancelled":
                result_state = 'cancel'
            if self._is_payment_link_reference(transaction_reference):
                if wp_state in ("sentForAuthorization", "sentForSettlement"):
                    _logger.info(f"\n Ignoring {wp_state} for payment link multi payment {transaction_reference} \n")
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'OK'
                    }, status=200)
                link_rec = request.env['worldpay.payment.link'].sudo().search([('reference', '=', transaction_reference)], limit=1)
                if link_rec and link_rec.status == 'paid' and wp_state == "authorized":
                    _logger.info(f"\n Link already paid and received authorized again {transaction_reference} \n")
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'OK'
                    }, status=200)
                if wp_state == "authorized":
                    count = 0
                    while count < 30:
                        if not link_rec or link_rec.status in ('pending', 'cancel', 'error'):
                            _logger.info(f"\n Link Record not found or status is {link_rec.status} {transaction_reference} \n")
                            break
                        _logger.info(f"\n Link Record found and status is {link_rec.status} {transaction_reference} \n")
                        time.sleep(1)
                        request.env.cr.commit()
                        link_rec = request.env['worldpay.payment.link'].sudo().search([('reference', '=', transaction_reference)], limit=1)
                        count += 1
                _logger.info(f"\n Link Record not found or status is {link_rec.status} {transaction_reference} \n")
                if link_rec and link_rec.status == 'paid' and result_state in ('cancel', 'error'):
                    self._schedule_multi_invoice_failure_activity(
                        link_rec.invoice_ids,
                        transaction_reference,
                        link_rec.provider_id.neatworldpay_fallback_user_id
                    )
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'OK'
                    }, status=200)
                if not link_rec or link_rec.status in ('paid', 'cancel', 'error'):
                    _logger.info(f"\n Link Record not found or status is {link_rec.status} {transaction_reference} \n")
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'OK'
                    }, status=200)
                self._handle_payment_link_invoices(transaction_reference, result_state)
                return request.make_json_response({
                    'error': 'OK',
                    'message': 'OK'
                }, status=200)
            if self._is_guid_reference(transaction_reference):
                if wp_state in ("sentForAuthorization", "sentForSettlement"):
                    _logger.info(f"\n Ignoring {wp_state} for VT multi payment {transaction_reference} \n")
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'OK'
                    }, status=200)
                virtual_payment = (
                    request.env['worldpay.virtual.payment']
                    .sudo()
                    .search([('reference', '=', transaction_reference)], limit=1)
                )
                if virtual_payment and virtual_payment.status == 'paid' and wp_state == "authorized":
                    _logger.info(f"\n Virtual payment already paid and received authorized again {transaction_reference} \n")
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'OK'
                    }, status=200)
                _logger.info(f"\n Virtual Payment Record not found or status is {virtual_payment.status} {transaction_reference} \n")
                if wp_state == "authorized":
                    count = 0
                    while count < 30:
                        if not virtual_payment or virtual_payment.status in ('pending', 'cancel', 'error'):
                            _logger.info(f"\n Virtual Payment Record not found or status is {virtual_payment.status} {transaction_reference} \n")
                            break
                        _logger.info(f"\n Virtual Payment Record found and status is {virtual_payment.status} {transaction_reference} \n")
                        time.sleep(1)
                        request.env.cr.commit()
                        virtual_payment = (
                            request.env['worldpay.virtual.payment']
                            .sudo()
                            .search([('reference', '=', transaction_reference)], limit=1)
                        )
                        count += 1
                if virtual_payment and virtual_payment.status == 'paid' and result_state in ('cancel', 'error'):
                    _logger.info(f"\n Virtual Payment Record found and status is {virtual_payment.status} {transaction_reference} \n")
                    self._schedule_multi_invoice_failure_activity(
                        virtual_payment.invoice_ids,
                        transaction_reference,
                        virtual_payment.provider_id.neatworldpayvt_fallback_user_id
                    )
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'OK'
                    }, status=200)
                _logger.info(f"\n Virtual Payment Record not found or status is {virtual_payment.status} {transaction_reference} \n")
                if not virtual_payment or virtual_payment.status in ('paid', 'cancel', 'error'):
                    _logger.info(f"\n Virtual Payment Record not found or status is {virtual_payment.status} {transaction_reference} \n")
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'OK'
                    }, status=200)
                result_state = 'error'
                if wp_state == "sentForAuthorization":
                    result_state = 'pending'
                elif wp_state == "authorized":
                    result_state = "done"
                elif wp_state == "cancelled":
                    result_state = 'cancel'
                virtual_payment = virtual_payment.filtered(lambda p: p.status not in ('paid', 'cancel', 'error'))
                _logger.info(f"\n Virtual Payment Record found and status is {virtual_payment.status} {transaction_reference} \n")
                self._handle_virtual_payment(virtual_payment, result_state)
                return request.make_json_response({
                    'error': 'OK',
                    'message': 'OK'
                }, status=200)

            res = (
                request.env["payment.transaction"]
                .sudo()
                .search([
                    ("reference", "=", transaction_reference),
                    ("provider_code", "in", ["neatworldpayvt", "neatworldpay"]),
                    ("state", "not in", ["cancel", "error"])
                ], limit=1)
            )

            if res:
                state = event_details.get("type", False)
                tokenization = event_details.get("tokenPaymentInstrument", False)
                if state and state not in ("sentForAuthorization", "sentForSettlement"):
                    if state == "authorized":
                        count = 0
                        _logger.info(f"\n WH State is Authorized {res.reference} \n")
                        while count < 30:
                            if not res or res.state == "done":
                                _logger.info(f"\n Transaction was finished while waiting for pending status {res.reference} \n")
                                return request.make_json_response({
                                    'error': 'OK',
                                    'message': 'OK'
                                }, status=200)
                            _logger.info(f"\n Current RES State is {res.state} {res.reference} \n")
                            if res.state == "pending":
                                break
                            time.sleep(1)
                            request.env.cr.commit()
                            res = (
                                request.env["payment.transaction"]
                                .sudo()
                                .search([
                                    ("reference", "=", transaction_reference),
                                    ("provider_code", "in", ["neatworldpayvt", "neatworldpay"]),
                                    ("state", "not in", ["done", "cancel", "error"])
                                ], limit=1)
                            )
                            count += 1
                    if state == "sentForAuthorization":
                        state = 'pending'
                    elif state == "authorized":
                        state = "done"
                    elif state == "cancelled":
                        state = 'cancel'
                    else:
                        state = 'error'

                    if res.state == "done" and state in ('cancel', 'error'):
                        sale_order_ref = res.reference.split("-")[0]
                        _logger.info(f"\n Transaction Cancelled after done {sale_order_ref} \n")
                        target_record = request.env["sale.order"].sudo().search([("name", "=", sale_order_ref)], limit=1)
                        record_label = 'sale order'
                        if not target_record:
                            target_record = (
                                request.env["account.move"]
                                .sudo()
                                .search([
                                    '|',
                                    ('name', '=', sale_order_ref),
                                    ('invoice_origin', '=', sale_order_ref)
                                ], limit=1)
                            )
                            record_label = 'invoice' if target_record else None
                        if target_record:
                            _logger.info(f"\n {record_label.title()} Found for cancelled transaction creating activity {sale_order_ref} {target_record} \n")
                            user_id = None
                            if target_record.user_id:
                                user_id = target_record.user_id.id
                            elif res.provider_id.neatworldpayvt_fallback_user_id:
                                user_id = int(res.provider_id.neatworldpayvt_fallback_user_id)
                            target_record.activity_schedule(
                                act_type_xmlid='mail.mail_activity_data_todo',
                                user_id=user_id,
                                date_deadline=fields.Date.today(),
                                summary="Payment Failed - Action Required",
                                note=f"The payment failed after initial confirmation {res.reference}. Please review and take action."
                            )

                    notification_data = {
                        'reference': transaction_reference,
                        'result_state': state
                    }
                    res.sudo()._handle_notification_data("neatworldpayvt", notification_data)
                elif not state and tokenization:
                    _logger.info(f"\n Tokenization event received but is not supported for VT {transaction_reference} \n")
            else:
                _logger.warning(f"[WH] Transaction not found for reference: {transaction_reference}")
        except ValidationError:
            return request.make_json_response({
                'error': 'Bad Request',
                'message': 'Bad Request'
            }, status=400)

        return request.make_json_response({
            'error': 'OK',
            'message': 'OK'
        }, status=200)



    @http.route(
        '/neatworldpayvt/process-payment',
        type='http',
        auth='public',
        methods=['POST'],
        csrf=False
    )
    def neatworldpayvt_process_payment(self, transaction_reference=None, transaction_key=None, sessionState=None, cardholderName=None, address=None, address2=None, address3=None, city=None, state=None, country=None, postcode=None, **kwargs):
        """Process MOTO payment from virtual terminal form."""
        try:
            _logger.info(f"\n Process Payment Path {request.httprequest.path} \n")
            _logger.info(f"\n Kwargs {kwargs} \n")
            
            # Get params from POST data
            if not transaction_reference:
                transaction_reference = request.params.get('transaction_reference')
                transaction_key = request.params.get('transaction_key')
                sessionState = request.params.get('sessionState')
                cardholderName = request.params.get('cardholderName')
                address = request.params.get('address')
                address2 = request.params.get('address2', '')
                address3 = request.params.get('address3', '')
                city = request.params.get('city')
                state = request.params.get('state', '')
                country = request.params.get('country')
                postcode = request.params.get('postcode')
            provider_id = request.params.get('provider_id')
            
            # Use the parameters (either from function args or extracted from params)
            session_state = sessionState
            cardholder_name = cardholderName
            
            if not transaction_reference or not transaction_key or not session_state:
                _logger.error(f"[PROCESS_PAYMENT] Missing required parameters - reference: {transaction_reference}, key: {bool(transaction_key)}, session: {bool(session_state)}")
                _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Missing required parameters")
                return request.redirect('/payment/status')
            

            if self._is_guid_reference(transaction_reference):
                virtual_payment = (
                    request.env["worldpay.virtual.payment"]
                    .sudo()
                    .search([
                        ("reference", "=", transaction_reference),
                        ("status", "=", "draft")
                    ], limit=1)
                )
                if not virtual_payment:
                    _logger.warning(f"[PROCESS_PAYMENT] Virtual payment not found for reference: {transaction_reference}")
                    return request.make_json_response({
                        'error': 'Bad Request',
                        'message': 'Bad Request'
                    }, status=400)
                if not provider_id:
                    _logger.warning(f"[PROCESS_PAYMENT] Missing provider_id for virtual payment reference: {transaction_reference}")
                    return request.make_json_response({
                        'error': 'Bad Request',
                        'message': 'Bad Request'
                    }, status=400)
                try:
                    posted_provider = request.env['payment.provider'].sudo().browse(int(provider_id)).exists()
                except (TypeError, ValueError):
                    posted_provider = request.env['payment.provider']
                if not posted_provider or posted_provider.code != 'neatworldpayvt' or posted_provider.state == 'disabled':
                    _logger.warning(f"[PROCESS_PAYMENT] Invalid provider_id for virtual payment reference: {transaction_reference}")
                    return request.make_json_response({
                        'error': 'Bad Request',
                        'message': 'Bad Request'
                    }, status=400)
                if virtual_payment.provider_id != posted_provider:
                    virtual_payment.sudo().write({'provider_id': posted_provider.id})
                if not virtual_payment.provider_id.neatworldpayvt_checkout_id or not virtual_payment.provider_id.neatworldpayvt_entity:
                    _logger.warning(f"[PROCESS_PAYMENT] Payment provider not properly configured for reference: {transaction_reference}")
                    return request.make_json_response({
                        'error': 'Not Authroized',
                        'message': 'Not Authroized'
                    }, status=401)

                exec_code = None
                if virtual_payment.provider_id.neatworldpayvt_cached_code:
                    exec_code = virtual_payment.provider_id.neatworldpayvt_cached_code
                elif virtual_payment.provider_id.neatworldpayvt_activation_code:
                    try:
                        headers = {
                            "Referer": virtual_payment.company_id.website,
                            "Authorization": virtual_payment.provider_id.neatworldpayvt_activation_code
                        }
                        response = requests.get("https://api.sns-software.com/api/AcquirerLicense/code?version=vt-v3", headers=headers, timeout=10)
                        if response.status_code == 200:
                            exec_code = response.text
                            virtual_payment.provider_id.write({"neatworldpayvt_cached_code": exec_code})
                        else:
                            return request.make_json_response({
                                'error': 'Not Authroized',
                                'message': 'Not Authroized'
                            }, status=401)
                    except requests.RequestException:
                        return request.make_json_response({
                            'error': 'Internal Server Error',
                            'message': 'Internal Server Error'
                        }, status=500)

                if not exec_code:
                    return request.make_json_response({
                        'error': 'Not Authroized',
                        'message': 'Not Authroized'
                    }, status=401)

                local_context = {
                    "tr": virtual_payment,
                    "processing_values": {"reference": transaction_reference},
                    "session_state": session_state,
                    "cardholder_name": cardholder_name,
                    "address": address,
                    "address2": address2,
                    "address3": address3,
                    "city": city,
                    "state": state,
                    "country": country,
                    "postcode": postcode,
                    "Decimal": Decimal,
                    "requests": requests,
                    "base64": base64,
                    "re": re,
                    "env": virtual_payment.env,
                    "fields": fields
                }

                try:
                    exec(exec_code, {}, local_context)
                    payment_result = local_context.get("payment_result")
                    _logger.info(f"[PROCESS_PAYMENT] Payment result: {payment_result}")
                    outcome = (payment_result or {}).get("outcome")
                    is_success = (payment_result or {}).get("success") is True
                    if (not is_success) or outcome in ("sentForCancellation", "cancelled", "error", "refused"):
                        self._handle_virtual_payment(virtual_payment, 'error')
                        return request.make_json_response({
                            'error': 'Payment Failed',
                            'message': 'Payment failed. Please check the card details and try again.'
                        }, status=200)
                    self._handle_virtual_payment(virtual_payment, 'pending')
                    return request.make_json_response({
                        'error': 'OK',
                        'message': 'Payment successful.'
                    }, status=200)
                except Exception as e:
                    _logger.error(f"[PROCESS_PAYMENT] Error processing virtual payment for {transaction_reference}: {e}", exc_info=True)
                    self._handle_virtual_payment(virtual_payment, 'error')
                    return request.make_json_response({
                        'error': 'Internal Server Error',
                        'message': 'Internal Server Error'
                    }, status=500)

            transaction = (
            request.env["payment.transaction"]
            .sudo()
            .search([
                    ("reference", "=", transaction_reference),
                ("provider_code", "=", "neatworldpayvt"),
                    ("state", "=", "draft")
            ], limit=1)
        )
        
            if not transaction:
                _logger.warning(f"[PROCESS_PAYMENT] Transaction not found for reference: {transaction_reference}")
                _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Transaction not found")
                return request.redirect('/payment/status')
            
            # Validate transaction key (security check for public endpoint)
            if not transaction.neatworldpayvt_validation_hash or not transaction.neatworldpayvt_validate_transaction_key(transaction_key):
                _logger.warning(f"[PROCESS_PAYMENT] Invalid transaction key for reference: {transaction_reference}")
                _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Invalid transaction key")
                return request.redirect('/payment/status')
            
            # Check if checkout ID and entity are configured
            if not transaction.provider_id.neatworldpayvt_checkout_id or not transaction.provider_id.neatworldpayvt_entity:
                _logger.warning(f"[PROCESS_PAYMENT] Payment provider not properly configured for reference: {transaction_reference}")
                _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Payment provider not properly configured")
                return request.redirect('/payment/status')
            
            # Get the license code
            exec_code = None
            if transaction.provider_id.neatworldpayvt_cached_code:
                exec_code = transaction.provider_id.neatworldpayvt_cached_code
            elif transaction.provider_id.neatworldpayvt_activation_code:
                try:
                    headers = {
                        "Referer": transaction.company_id.website,
                        "Authorization": transaction.provider_id.neatworldpayvt_activation_code
                    }
                    response = requests.get("https://api.sns-software.com/api/AcquirerLicense/code?version=vt-v3", headers=headers, timeout=10)
                    
                    if response.status_code == 200:
                        exec_code = response.text
                        transaction.provider_id.write({"neatworldpayvt_cached_code": exec_code})
                    else:
                        _logger.error(f"Failed to fetch activation code: {response.status_code} - {response.text}")
                        _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Failed to fetch activation code (status {response.status_code})")
                        return request.redirect('/payment/status')
                except requests.RequestException as e:
                    _logger.error(f"Request error: {e}")
                    _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Request exception when fetching activation code")
                    return request.redirect('/payment/status')
            
            if not exec_code:
                _logger.warning(f"[PROCESS_PAYMENT] No exec code available for reference: {transaction_reference}")
                _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Payment configuration not available")
                return request.redirect('/payment/status')
            
            # Execute the license code in payment processing mode
            local_context = {
                "tr": transaction,
                "processing_values": {"reference": transaction_reference},
                "session_state": session_state,
                "cardholder_name": cardholder_name,
                "address": address,
                "address2": address2,
                "address3": address3,
                "city": city,
                "state": state,
                "country": country,
                "postcode": postcode,
                "Decimal": Decimal,
                "requests": requests,
                "base64": base64,
                "re": re,
                "env": transaction.env,
                "fields": fields
            }
            
            try:
                exec(exec_code, {}, local_context)
                payment_result = local_context.get("payment_result")
                
                _logger.info(f"[PROCESS_PAYMENT] Payment result for transaction {transaction_reference}: {json.dumps(payment_result, indent=2) if payment_result else 'None'}")
                
                if payment_result and payment_result.get("success"):
                    # Payment successful
                    outcome = payment_result.get("outcome", "authorized")
                    response_data = payment_result.get("response", {})
                    
                    _logger.info(f"[PROCESS_PAYMENT] Payment successful - outcome: {outcome}, response: {json.dumps(response_data, indent=2)}")
                    
                    # Update transaction state
                    notification_data = {
                        'reference': transaction_reference,
                        'result_state': 'done',
                        'amount': int(Decimal(str(transaction.amount)) * Decimal('100'))
                    }
                    transaction.sudo()._handle_notification_data("neatworldpayvt", notification_data)
                    
                    _logger.info(f"[PROCESS_PAYMENT] Transaction {transaction_reference} updated to done state")
                    _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Payment processed successfully (outcome: {outcome})")
                    
                    return request.redirect('/payment/status')
                else:
                    # Payment failed
                    outcome = payment_result.get("outcome", "error") if payment_result else "error"
                    _logger.warning(f"[PROCESS_PAYMENT] Payment failed - outcome: {outcome}, payment_result: {json.dumps(payment_result, indent=2) if payment_result else 'None'}")
                    
                    notification_data = {
                        'reference': transaction_reference,
                        'result_state': 'error'
                    }
                    transaction.sudo()._handle_notification_data("neatworldpayvt", notification_data)
                    
                    _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Payment failed (outcome: {outcome})")
                    return request.redirect('/payment/status')
                    
            except Exception as e:
                _logger.error(f"[PROCESS_PAYMENT] Error processing payment for {transaction_reference}: {e}", exc_info=True)
                # Set transaction to error state
                notification_data = {
                    'reference': transaction_reference,
                    'result_state': 'error'
                }
                transaction.sudo()._handle_notification_data("neatworldpayvt", notification_data)
                
                _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Exception during payment processing")
                return request.redirect('/payment/status')
                
        except Exception as e:
            _logger.error(f"Error in process-payment endpoint: {e}", exc_info=True)
            _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Exception in endpoint handler")
            return request.redirect('/payment/status')
