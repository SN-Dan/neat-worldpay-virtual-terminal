/** @odoo-module */

import { _t } from '@web/core/l10n/translation';
import paymentForm from '@payment/js/payment_form';

paymentForm.include({

     /**
     * Open the inline form of the selected payment option, if any.
     *
     * @override method from @payment/js/payment_form
     * @private
     * @param {Event} ev
     * @return {void}
     */
    async _selectPaymentOption(ev) {
        await this._super(...arguments);
    },
        /**
     * Prepare the inline form of Stripe for direct payment.
     *
     * @override method from @payment/js/payment_form
     * @private
     * @param {number} providerId - The id of the selected payment option's provider.
     * @param {string} providerCode - The code of the selected payment option's provider.
     * @param {number} paymentOptionId - The id of the selected payment option
     * @param {string} paymentMethodCode - The code of the selected payment method, if any.
     * @param {string} flow - The online payment flow of the selected payment option.
     * @return {void}
     */
    async _prepareInlineForm(providerId, providerCode, paymentOptionId, paymentMethodCode, flow) {
        if (providerCode !== 'neatworldpayvt') {
            this._super(...arguments);
            return;
        }
        
        if (flow === 'token') {
            return;
        }

        this._setPaymentFlow('direct');
    },

    // #=== PAYMENT FLOW ===#

    /**
     * feedback from a payment provider and redirect the customer to the status page.
     *
     * @override method from payment.payment_form
     * @private
     * @param {string} providerCode - The code of the selected payment option's provider.
     * @param {number} paymentOptionId - The id of the selected payment option.
     * @param {string} paymentMethodCode - The code of the selected payment method, if any.
     * @param {object} processingValues - The processing values of the transaction.
     * @return {void}
     */
    async _processDirectFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'neatworldpayvt') {
            this._super(...arguments);
            return;
        }
        
        if(!processingValues.transaction_key) {
            alert("Worldpay integration is not active. Please update the activation code.");
            return;
        }
        
        this.call('ui', 'unblock');
        const popup = document.querySelector('#neatworldpayvt_popup');
        if (popup) {
            popup.style.display = 'block';
            this._populateVirtualTerminalContainer(processingValues);
            
            // Start polling for payment status
            this._startPaymentPolling(processingValues.transaction_key, processingValues.transaction_reference);
        }
    },
    /**
     * Redirect the customer to the status route.
     *
     * @override method from payment.payment_form
     * @private
     * @param {string} providerCode - The code of the selected payment option's provider.
     * @param {number} paymentOptionId - The id of the selected payment option.
     * @param {string} paymentMethodCode - The code of the selected payment method, if any.
     * @param {object} processingValues - The processing values of the transaction.
     * @return {void}
     */
    async _processTokenFlow(providerCode, paymentOptionId, paymentMethodCode, processingValues) {
        if (providerCode !== 'neatworldpayvt') {
            this._super(...arguments);
            return;
        }
        
        if(!processingValues.transaction_key) {
            alert("Worldpay integration is not active. Please update the activation code.");
            return;
        }
        
        this.call('ui', 'unblock');
        const popup = document.querySelector('#neatworldpayvt_popup');
        if (popup) {
            popup.style.display = 'block';
            this._populateVirtualTerminalContainer(processingValues);
            
            // Start polling for payment status
            this._startPaymentPolling(processingValues.transaction_key, processingValues.transaction_reference);
        }
    },

    /**
     * Populate the virtual terminal container with transaction reference and copy functionality.
     *
     * @private
     * @param {object} processingValues - The processing values of the transaction
     * @return {undefined}
     */
    _populateVirtualTerminalContainer(processingValues) {
        const container = document.querySelector('#neatworldpayvt-container');
        if (!container) return;

        const transactionReference = processingValues.transaction_reference || 'N/A';
        
        // Check if transaction reference ends with dash and number (e.g., "originalref-1")
        const isIterationReference = /-.+$/.test(transactionReference);
        
        container.innerHTML = `
            <div style="text-align: center; padding: 20px;">
                <h3 style="margin-bottom: 20px; color: #334;">Virtual Terminal Payment</h3>
                
                <div style="background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 15px; margin-bottom: 20px;">
                    <p style="margin-bottom: 15px; color: #667; font-size: 14px;">
                        Copy this reference and paste it when you make the virtual terminal payment as a reference:
                    </p>
                    
                    <div style="display: flex; align-items: center; justify-content: center; gap: 10px; background: white; border: 1px solid #ced4da; border-radius: 6px; padding: 12px;">
                        <input type="text" 
                               id="transaction-reference-input" 
                               value="${transactionReference}" 
                               readonly 
                               style="border: none; outline: none; background: transparent; font-family: monospace; font-size: 16px; color: #333; flex: 1; text-align: center;"
                        />
                        <button type="button" 
                                id="copy-reference-btn" 
                                style="background: #007bff; color: white; border: none; border-radius: 4px; padding: 8px 16px; cursor: pointer; font-size: 14px; transition: background-color 0.2s;"
                                onclick="copyTransactionReference()">
                            Copy
                        </button>
                    </div>
                </div>
                
                ${isIterationReference ? `
                <div style="background: #fff3cd; border: 1px solid #ffeaa7; border-radius: 6px; padding: 15px; margin-bottom: 20px;">
                    <p style="margin: 0; color: #856404; font-size: 13px; font-style: italic;">
                        ℹ️ If you have already made a payment with an older iteration of this reference, we will detect it and sync it.
                    </p>
                </div>
                ` : ''}
                
                <div id="polling-status" style="display: none; background: #e7f3ff; border: 1px solid #b3d9ff; border-radius: 6px; padding: 12px; margin: 15px 0; text-align: center; color: #0056b3; font-size: 14px;">
                    Checking payment status...
                </div>
                
                <div style="margin-top: 20px;">
                    <button type="button" 
                            onclick="closeVirtualTerminalPopup()" 
                            style="background: #6c757d; color: white; border: none; border-radius: 4px; padding: 10px 20px; cursor: pointer; font-size: 14px;">
                        Cancel
                    </button>
                </div>
            </div>
        `;

        // Add the copy functionality to the global scope
        window.copyTransactionReference = async function() {
            const input = document.getElementById('transaction-reference-input');
            if (input) {
                try {
                    // Use modern Clipboard API
                    await navigator.clipboard.writeText(input.value);
                    
                    const button = document.getElementById('copy-reference-btn');
                    if (button) {
                        const originalText = button.textContent;
                        button.textContent = 'Copied!';
                        button.style.background = '#28a745';
                        
                        setTimeout(() => {
                            button.textContent = originalText;
                            button.style.background = '#007bff';
                        }, 2000);
                    }
                } catch (err) {
                    // Fallback for older browsers or when clipboard API is not available
                    input.select();
                    input.setSelectionRange(0, 99999); // For mobile devices
                    try {
                        document.execCommand('copy');
                        
                        const button = document.getElementById('copy-reference-btn');
                        if (button) {
                            const originalText = button.textContent;
                            button.textContent = 'Copied!';
                            button.style.background = '#28a745';
                            
                            setTimeout(() => {
                                button.textContent = originalText;
                                button.style.background = '#007bff';
                            }, 2000);
                        }
                    } catch (fallbackErr) {
                        console.error('Failed to copy text: ', fallbackErr);
                        alert('Failed to copy reference. Please copy it manually.');
                    }
                }
            }
        };

        window.closeVirtualTerminalPopup = function() {
            const popup = document.querySelector('#neatworldpayvt_popup');
            if (popup) {
                popup.style.display = 'none';
            }
            
            // Stop polling when popup is closed
            if (window._currentPaymentForm && window._currentPaymentForm._stopPaymentPolling) {
                window._currentPaymentForm._stopPaymentPolling();
            }
        };
    },

    /**
     * Start polling the payment endpoint to check for payment completion.
     *
     * @private
     * @param {string} transactionKey - The transaction key for validation
     * @param {string} transactionReference - The transaction reference
     * @return {undefined}
     */
    _startPaymentPolling(transactionKey, transactionReference) {
        const self = this;
        let pollCount = 0;
        const maxPolls = 60; // Maximum 5 minutes (60 * 5 seconds)
        const pollInterval = 5000; // 5 seconds
        
        // Store polling state to allow cancellation
        this._pollingActive = true;
        this._pollingTimeout = null;
        
        // Store reference to this instance for the close function
        window._currentPaymentForm = this;
        
        const pollStatus = async function() {
            // Check if polling has been cancelled
            if (!self._pollingActive) {
                return;
            }
            
            try {
                pollCount++;
                self._updatePollingStatus(`Checking payment status... (${pollCount}/${maxPolls})`);
                var rpc = self.rpc ? self.rpc : self.env.services.rpc;
                // Call the controller endpoint using Odoo 17+ RPC format
                const response = await rpc(`/neatworldpayvt/result/${transactionReference}`, {
                    transaction_key: transactionKey
                });
                
                // Check the response status in the JSON payload
                if (response.status === 200) {
                    // Payment was successful and we should redirect
                    self._updatePollingStatus('Payment completed! Redirecting...');
                    setTimeout(() => {
                        window.location.href = '/payment/status';
                    }, 1000);
                } else if (response.status === 404) {
                    // Payment not found yet, continue polling
                    if (pollCount >= maxPolls) {
                        // Timeout reached - show error popup
                        self._showErrorPopup('Timeout: Payment not completed within expected time.');
                        return;
                    }
                    
                    // Continue polling after delay
                    self._pollingTimeout = setTimeout(pollStatus, pollInterval);
                } else {
                    // Handle other error status codes
                    const errorMessage = response.data?.error || 'Payment verification failed';
                    self._showErrorPopup(`Error: ${errorMessage}`);
                }
                
            } catch (error) {
                console.log('Polling attempt failed:', error);
                
                // Check if polling has been cancelled
                if (!self._pollingActive) {
                    return;
                }
                
                // For network errors or other exceptions, stop polling and show error popup
                const errorMessage = error.message || 'Payment verification failed';
                self._showErrorPopup(`Error: ${errorMessage}`);
            }
        };
        
        // Start polling
        pollStatus();
    },

    /**
     * Update the polling status in the popup.
     *
     * @private
     * @param {string} status - The status message to display
     * @return {undefined}
     */
    _updatePollingStatus(status) {
        const statusElement = document.getElementById('polling-status');
        if (statusElement) {
            statusElement.textContent = status;
            statusElement.style.display = 'block';
        }
    },

    /**
     * Stop the payment polling.
     *
     * @private
     * @return {undefined}
     */
    _stopPaymentPolling() {
        this._pollingActive = false;
        if (this._pollingTimeout) {
            clearTimeout(this._pollingTimeout);
            this._pollingTimeout = null;
        }
        console.log('Payment polling stopped');
    },

    /**
     * Show error popup and close the current virtual terminal popup.
     *
     * @private
     * @param {string} errorMessage - The error message to display
     * @return {undefined}
     */
    _showErrorPopup(errorMessage) {
        // Stop polling first
        this._stopPaymentPolling();
        
        // Close the current virtual terminal popup
        const currentPopup = document.querySelector('#neatworldpayvt_popup');
        if (currentPopup) {
            currentPopup.style.display = 'none';
        }
        
        // Create and show error popup
        const errorPopup = document.createElement('div');
        errorPopup.id = 'neatworldpayvt_error_popup';
        errorPopup.style.cssText = `
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            z-index: 9999;
        `;
        
        errorPopup.innerHTML = `
            <div style="
                background: white;
                border-radius: 8px;
                padding: 30px;
                max-width: 500px;
                width: 90%;
                text-align: center;
                box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
            ">
                <div style="
                    width: 60px;
                    height: 60px;
                    background: #dc3545;
                    border-radius: 50%;
                    margin: 0 auto 20px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                ">
                    <span style="color: white; font-size: 24px;">⚠️</span>
                </div>
                
                <h3 style="
                    margin: 0 0 15px 0;
                    color: #dc3545;
                    font-size: 18px;
                ">Payment Error</h3>
                
                <p style="
                    margin: 0 0 25px 0;
                    color: #666;
                    font-size: 14px;
                    line-height: 1.5;
                ">${errorMessage}</p>
                
                <button type="button" 
                        onclick="closeErrorPopup()" 
                        style="
                            background: #dc3545;
                            color: white;
                            border: none;
                            border-radius: 4px;
                            padding: 12px 24px;
                            cursor: pointer;
                            font-size: 14px;
                            transition: background-color 0.2s;
                        "
                        onmouseover="this.style.background='#c82333'"
                        onmouseout="this.style.background='#dc3545'">
                    Close
                </button>
            </div>
        `;
        
        // Add to body
        document.body.appendChild(errorPopup);
        
        // Add close function to global scope
        window.closeErrorPopup = function() {
            const popup = document.getElementById('neatworldpayvt_error_popup');
            if (popup) {
                popup.remove();
            }
        };
    },

});