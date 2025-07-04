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
        debugger;
        this.call('ui', 'unblock');
        const popup = document.querySelector('#neatworldpayvt_popup');
        if (popup) {
            popup.style.display = 'block';
            this._populateVirtualTerminalContainer(processingValues);
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
        };
    },

});