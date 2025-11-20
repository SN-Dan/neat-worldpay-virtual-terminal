# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.
# This module extends Odoo's payment framework.
# Odoo is a trademark of Odoo S.A.

import base64
import json
import logging
import re
import requests
from decimal import Decimal
from odoo.http import request
from odoo import _, http, fields
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class NeatWorldpayVTController(http.Controller):


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
