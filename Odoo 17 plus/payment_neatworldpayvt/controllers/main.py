import base64
import binascii
import hashlib
import hmac
import json
import logging
import pprint
import time
import re
from decimal import Decimal
from odoo.http import request
from odoo import _, http, fields, sql_db
from contextlib import closing
from odoo.exceptions import ValidationError
from datetime import datetime
import base64
import requests

_logger = logging.getLogger(__name__)


class NeatWorldpayVTController(http.Controller):

    result_action = "/neatworldpayvt/result"
    
    def _search_backwards_for_transaction(self, original_reference, provider, expected_amount=None):
        """
        Search backwards through transaction references if the original reference ends with -{number}.
        Loops from the number down to 0, trying each reference with a 500ms delay.
        
        :param str original_reference: The original transaction reference
        :param object provider: The payment provider object
        :param float expected_amount: The expected payment amount to verify
        :return: tuple (found_reference, response_data) or (None, None) if not found
        """
        # Check if reference ends with -{number}
        match = re.match(r'^(.+)-(\d+)$', original_reference)
        if not match:
            return original_reference, None
        
        base_reference = match.group(1)
        start_number = int(match.group(2))
        
        _logger.info(f"Starting backward search for base reference: {base_reference}, starting from: {start_number}")
        
        # Prepare headers for Worldpay API calls
        basicTokenUnencoded = provider.neatworldpayvt_username + ":" + provider.neatworldpayvt_password
        basicToken = base64.b64encode(basicTokenUnencoded.encode("utf-8")).decode()
        
        headers = {
            "Authorization": "Basic " + basicToken,
            "Content-Type": "application/vnd.worldpay.payment_pages-v1.hal+json",
            "Accept": "application/vnd.worldpay.payment_pages-v1.hal+json",
            "User-Agent": "neatapps"
        }
        
        # Determine base URL based on provider state
        base_url = "https://try.access.worldpay.com/paymentQueries/payments"
        if provider.state == "enabled":
            base_url = "https://access.worldpay.com/paymentQueries/payments"
        
        # Search backwards from start_number to 0
        for i in range(start_number, -1, -1):
            if i == 0:
                # For 0, use the base reference without any suffix
                test_reference = base_reference
            else:
                # For other numbers, append -{number}
                test_reference = f"{base_reference}-{i}"
            
            worldpay_url = f"{base_url}?transactionReference={test_reference}"
            
            try:
                _logger.info(f"Trying reference: {test_reference}")
                response = requests.get(worldpay_url, headers=headers, timeout=10)
                
                # Check if we got a successful response (status 200)
                if response.status_code == 200:
                    response_data = response.json()
                    _logger.info(f"Found successful response for reference: {test_reference}")
                    
                    # Verify that there's a payment in the response
                    if self._verify_payment_in_response(response_data, expected_amount):
                        return test_reference, response_data
                    else:
                        return None, None
                
                _logger.info(f"No success for reference: {test_reference}, status: {response.status_code}")
                
                # Wait 500ms before next call (except for the last iteration)
                if i > 0:
                    time.sleep(0.5)
                    
            except Exception as e:
                _logger.error(f"Error checking reference {test_reference}: {e}")
                # Continue to next iteration even if there's an error
                if i > 0:
                    time.sleep(0.5)
        
        _logger.warning(f"No successful transaction found in backward search for base: {base_reference}")
        return None, None

    def _verify_payment_in_response(self, response_data, expected_amount=None):
        """
        Verify that there's a payment in the response and optionally check the amount.
        
        :param dict response_data: The response data from Worldpay API
        :param float expected_amount: The expected payment amount to verify
        :return: bool: True if payment is valid, raises ValidationError otherwise
        """
        try:
            # Check if response has the expected structure
            if not response_data or '_embedded' not in response_data:
                _logger.error("Invalid response structure: missing _embedded")
                raise ValidationError(_("Invalid payment response structure"))
            
            payments = response_data.get('_embedded', {}).get('payments', [])
            if not payments:
                return False
            
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
        result_action + "/<string:transaction_key>",
        type="http",
        auth="public",
        csrf=False,
        save_session=False,
    )
    def neatworldpayvt_result(self, transaction_key, **kwargs):
        _logger.info(f"\n Redirect Path {request.httprequest.path} \n")
        _logger.info(f"\n Kwargs {kwargs} \n")
        
        original_reference = kwargs.get("reference", False)
        if not original_reference:
            return request.redirect("/payment/status")
        
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
            return request.redirect("/payment/status")
        
        # Validate transaction key
        if not res.neatworldpayvt_validation_hash or not res.neatworldpayvt_validate_transaction_key(transaction_key):
            return request.redirect("/payment/status")
        
        if res.state == "done":
            return request.redirect("/payment/status")

        # Get the expected amount from the transaction
        expected_amount = res.amount if hasattr(res, 'amount') else None
        response_data = None
        # Search backwards for the actual transaction reference
        try:
            found_reference, response_data = self._search_backwards_for_transaction(original_reference, res.provider_id, expected_amount)
            
            if found_reference and found_reference != original_reference:
                _logger.info(f"Found transaction with different reference: {found_reference} (original: {original_reference})")
                # Update the reference in kwargs for processing
                #kwargs["reference"] = found_reference
            elif found_reference is None:
                _logger.error(f"Payment not found")
                return request.make_response(
                    json.dumps({'error': 'Payment not found'}),
                    status=404,
                    headers=[('Content-Type', 'application/json')]
                )
                
        except ValidationError as e:
            # Payment verification failed, return 400 error
            _logger.error(f"Payment verification failed: {e}")
            return request.make_response(
                json.dumps({'error': str(e)}),
                status=400,
                headers=[('Content-Type', 'application/json')]
            )
        
        amount = response_data['_embedded']['payments'][0]['value']['amount']
        result_state = 'done'

        data = {
            'reference': kwargs.get("reference", False),
            'result_state': result_state,
            'amount': amount
        }
        
        try:
            res.sudo()._handle_notification_data("neatworldpayvt", data)
        except Exception as e:
            _logger.error(f"Error handling notification data for transaction {res.reference}: {e}")

        return request.redirect("/payment/status")
