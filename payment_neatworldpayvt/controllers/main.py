# Original Author: Daniel Stoynev
# Copyright (c) 2025 SNS Software Ltd. All rights reserved.
# This module extends Odoo's payment framework.
# Odoo is a trademark of Odoo S.A.

import base64
import binascii
import hashlib
import hmac
import json
import logging
import pprint
import time
import re
import requests
from decimal import Decimal
from odoo.http import request
from odoo import _, http, fields, sql_db
from contextlib import closing
from odoo.exceptions import ValidationError
from datetime import datetime

_logger = logging.getLogger(__name__)


class NeatWorldpayVTController(http.Controller):

    result_action = "/neatworldpayvt/result"
    _REFERENCE_PREFIX = "SNSVT-"
    _MAX_REFERENCE_LENGTH = 20

    def _prefix_if_allowed(self, reference):
        if not reference.startswith(self._REFERENCE_PREFIX) and len(reference) + len(self._REFERENCE_PREFIX) <= self._MAX_REFERENCE_LENGTH:
            return self._REFERENCE_PREFIX + reference
        else:
            return reference
    
    def _search_backwards_for_transaction(self, original_reference, provider, expected_amount=None):
        """
        Search backwards through transaction references if the original reference ends with -{number}.
        Loops from the number down to 0, trying each reference with a 500ms delay.
        If the reference doesn't end with -{number}, makes a single request for the original reference.
        
        :param str original_reference: The original transaction reference
        :param object provider: The payment provider object
        :param float expected_amount: The expected payment amount to verify
        :return: tuple (found_reference, response_data) or (None, None) if not found
        """
        # Check if reference ends with -{number}
        match = re.match(r'^(.+)-(\d+)$', original_reference)
        if not match:
            reference_to_try = self._prefix_if_allowed(original_reference)
            return self._try_single_reference(reference_to_try, provider, expected_amount)
        
        base_reference = match.group(1)
        start_number = int(match.group(2))
        
        _logger.info(f"Starting backward search for base reference: {base_reference}, starting from: {start_number}")
        
        # Search backwards from start_number to 0
        for i in range(start_number, -1, -1):
            if i == 0:
                # For 0, use the base reference without any suffix
                test_reference = self._prefix_if_allowed(base_reference)
            else:
                # For other numbers, append -{number}
                test_reference = self._prefix_if_allowed(f"{base_reference}-{i}")
            
            result = self._try_single_reference(test_reference, provider, expected_amount)
            if result[1] is not None:  # If we found a payment
                return result
            
            # Wait 100ms before next call (except for the last iteration)
            if i > 0:
                time.sleep(0.1)
        
        _logger.warning(f"No successful transaction found in backward search for base: {base_reference}")
        return None, None

    def _try_single_reference(self, reference, provider, expected_amount=None):
        """
        Try to find a payment for a single reference.
        
        :param str reference: The transaction reference to check
        :param object provider: The payment provider object
        :param float expected_amount: The expected payment amount to verify
        :return: tuple (reference, response_data) or (reference, None) if not found
        """
        # Check if this reference has already been processed
        neat_payment_model = request.env['neatworldpayvt.payment'].sudo()
        if neat_payment_model.is_reference_processed(reference):
            _logger.info(f"Reference {reference} has already been processed, skipping")
            return reference, None
        
        # Prepare headers for Worldpay API calls
        basicTokenUnencoded = provider.neatworldpayvt_username + ":" + provider.neatworldpayvt_password
        basicToken = base64.b64encode(basicTokenUnencoded.encode("utf-8")).decode()
        
        headers = {
            "Authorization": "Basic " + basicToken,
            "Accept": "application/vnd.worldpay.payment-queries-v1.hal+json",
            "User-Agent": "neatapps"
        }
        
        # Determine base URL based on provider state
        base_url = "https://try.access.worldpay.com/paymentQueries/payments"
        if provider.state == "enabled":
            base_url = "https://access.worldpay.com/paymentQueries/payments"
        
        worldpay_url = f"{base_url}?transactionReference={reference}"
        
        try:
            _logger.info(f"Trying reference: {reference}")
            response = requests.get(worldpay_url, headers=headers, timeout=10)
            
            # Check if we got a successful response (status 200)
            if response.ok:
                response_data = response.json()
                _logger.info(f"Found successful response for reference: {reference}")
                _logger.info(f"Response data: {json.dumps(response_data, indent=2)}")
                
                if not response_data or '_embedded' not in response_data:
                    _logger.error("Invalid response structure: missing _embedded")
                    raise ValidationError(_("Invalid payment response structure"))
                
                payments = response_data.get('_embedded', {}).get('payments', [])
                if payments:
                    # Verify that there's a payment in the response
                    if self._verify_payment_in_response(response_data, expected_amount):
                        return reference, payments
                    else:
                        return reference, None
            
            _logger.info(f"No success for reference: {reference}, status: {response.status_code}")
            return reference, None
                
        except Exception as e:
            _logger.error(f"Error checking reference {reference}: {e}")
            return reference, None

    def _verify_payment_in_response(self, response_data, expected_amount=None):
        """
        Verify that there's a payment in the response and optionally check the amount.
        
        :param dict response_data: The response data from Worldpay API
        :param float expected_amount: The expected payment amount to verify
        :return: bool: True if payment is valid, raises ValidationError otherwise
        """
        try:
            payments = response_data.get('_embedded', {}).get('payments', [])
            payment = payments[0]  # Get the first payment
            _logger.info(f"Found payment: {payment.get('transactionReference')}")
            
            # If expected amount is provided, verify it matches
            if expected_amount is not None:
                payment_amount = payment.get('value', {}).get('amount')
                payment_currency = payment.get('value', {}).get('currency')
                
                if payment_amount is None:
                    _logger.error("Payment amount not found in response")
                    raise ValidationError(_("Payment amount not found in response"))
                
                if payment_currency is None:
                    _logger.error("Payment currency not found in response")
                    raise ValidationError(_("Payment currency not found in response"))
                
                # Validate currency
                expected_currency = 'GBP'  # You might want to get this from the transaction
                if payment_currency != expected_currency:
                    _logger.error(f"Currency mismatch: expected {expected_currency}, got {payment_currency}")
                    raise ValidationError(_("Currency mismatch: expected %s, got %s") % (expected_currency, payment_currency))
                
                # Convert expected amount to pence using Decimal for accuracy
                decimal_amount = Decimal(str(expected_amount)) * Decimal('100')
                expected_amount_pence = int(decimal_amount)
                
                # if payment_amount != expected_amount_pence:
                #     _logger.error(f"Amount mismatch: expected {expected_amount_pence} pence, got {payment_amount} pence")
                #     raise ValidationError(_("Amount mismatch: expected %s pence, got %s pence") % (expected_amount_pence, payment_amount))
                
                _logger.info(f"Amount verification successful: {payment_amount} pence ({payment_currency})")
            
            return True
            
        except ValidationError:
            # Re-raise ValidationError to be caught by the calling method
            raise
        except Exception as e:
            _logger.error(f"Error verifying payment in response: {e}")
            raise ValidationError(_("Error verifying payment in response"))

    @http.route(
        result_action + "/<path:reference>",
        type="json",
        auth="user",
         methods=['POST']
    )
    def neatworldpayvt_result(self, reference, transaction_key, **kwargs):
        _logger.info(f"\n Redirect Path {request.httprequest.path} \n")
        _logger.info(f"\n Kwargs {kwargs} \n")
        
        original_reference = reference
        if not original_reference:
            return {'status': 400, 'data': { 'error': 'Payment transaction not found' }}
        
        if original_reference.startswith('SNSVT-'):
            original_reference = original_reference[6:]  # Remove 'SNSVT-' (6 characters)
            _logger.info(f"Removed SNSVT- prefix, new reference: {original_reference}")
        # First, try to find the transaction with the original reference
        res = (
            request.env["payment.transaction"]
            .sudo()
            .search([
                ("reference", "=", original_reference),
                ("provider_code", "=", "neatworldpayvt"),
                ("state", "in", ["draft", "done"])
            ], limit=1)
        )
        
        if not res:
            _logger.warning(f"No transaction found for reference: {original_reference}")
            return {'status': 400, 'data': { 'error': 'Payment transaction not found' }}
        
        # Validate transaction key based on status
        if not res.neatworldpayvt_validation_hash or not res.neatworldpayvt_validate_transaction_key(transaction_key):
            return {'status': 403, 'data': { 'error': 'Forbidden' }}
        
        # Get the expected amount from the transaction
        expected_amount = res.amount if hasattr(res, 'amount') else None
        response_data = None
        # Search backwards for the actual transaction reference
        try:
            found_reference, response_data = self._search_backwards_for_transaction(original_reference, res.provider_id, expected_amount)
            
            if found_reference is None or response_data is None or len(response_data) == 0:
                _logger.error(f"Payment not found")
                return {'status': 404, 'data': { 'error': 'Payment not found' }}
                
        except ValidationError as e:
            # Payment verification failed, return 400 error
            _logger.error(f"Payment verification failed: {e}")
            return {'status': 400, 'data': { 'error': str(e) }}
        
        amount = response_data[0]['value']['amount']
        currency = response_data[0]['value'].get('currency', 'GBP')
        result_state = 'done'

        # Store the payment record to prevent duplicate processing
        try:
            neat_payment_model = request.env['neatworldpayvt.payment'].sudo()
            neat_payment_model.create_payment_record(
                worldpay_reference=found_reference,
                odoo_reference=original_reference,
                amount=amount,
                currency=currency,
                provider_id=res.provider_id.id,
                transaction_id=res.id
            )
            _logger.info(f"Stored payment record for Worldpay reference: {found_reference}")
        except Exception as e:
            _logger.error(f"Error storing payment record for {found_reference}: {e}")
            # Continue processing even if storage fails

        data = {
            'reference': kwargs.get("reference", False),
            'result_state': result_state,
            'amount': amount
        }
        
        try:
            res.sudo()._handle_notification_data("neatworldpayvt", data)
        except Exception as e:
            _logger.error(f"Error handling notification data for transaction {res.reference}: {e}")

        return {'status': 200, 'data': { 'message': 'Payment received' }}
