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
                    res.sudo()._process("neatworldpayvt", notification_data)
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
            
            # Use the parameters (either from function args or extracted from params)
            session_state = sessionState
            cardholder_name = cardholderName
            
            if not transaction_reference or not transaction_key or not session_state:
                _logger.error(f"[PROCESS_PAYMENT] Missing required parameters - reference: {transaction_reference}, key: {bool(transaction_key)}, session: {bool(session_state)}")
                _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Missing required parameters")
                return request.redirect('/payment/status')
            
            # Find the transaction
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
                    transaction.sudo()._process("neatworldpayvt", notification_data)
                    
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
                    transaction.sudo()._process("neatworldpayvt", notification_data)
                    
                    _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Payment failed (outcome: {outcome})")
                    return request.redirect('/payment/status')
                    
            except Exception as e:
                _logger.error(f"[PROCESS_PAYMENT] Error processing payment for {transaction_reference}: {e}", exc_info=True)
                # Set transaction to error state
                notification_data = {
                    'reference': transaction_reference,
                    'result_state': 'error'
                }
                transaction.sudo()._process("neatworldpayvt", notification_data)
                
                _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Exception during payment processing")
                return request.redirect('/payment/status')
                
        except Exception as e:
            _logger.error(f"Error in process-payment endpoint: {e}", exc_info=True)
            _logger.info(f"[PROCESS_PAYMENT] Redirecting to /payment/status - Reason: Exception in endpoint handler")
            return request.redirect('/payment/status')
