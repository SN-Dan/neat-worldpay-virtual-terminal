/** @odoo-module */

import { _t } from '@web/core/l10n/translation';
import checkoutForm from 'payment.checkout_form';
import manageForm from 'payment.manage_form';

const neatWorldpayvtMixin = {
    /**
     * Prepare the provider-specific inline form of the selected payment option.
     *
     * For a provider to manage an inline form, it must override this method. When the override
     * is called, it must lookup the parameters to decide whether it is necessary to prepare its
     * inline form. Otherwise, the call must be sent back to the parent method.
     *
     * @private
     * @param {string} code - The code of the selected payment option's provider
     * @param {number} paymentOptionId - The id of the selected payment option
     * @param {string} flow - The online payment flow of the selected payment option
     * @return {Promise}
     */
    _prepareInlineForm: function (code, paymentOptionId, flow) {
        if (code !== 'neatworldpayvt') {
            this._super(...arguments);
            return;
        }
        
        if (flow === 'token') {
            return; // No elements for tokens.
        }

        this._setPaymentFlow('direct');
    },
    
    // #=== PAYMENT FLOW ===#

    /**
     * Show the virtual terminal popup with transaction reference.
     *
     * @override method from payment.payment_form_mixin
     * @private
     * @param {string} code - The code of the payment option
     * @param {number} paymentOptionId - The id of the payment option handling the transaction
     * @param {object} processingValues - The processing values of the transaction
     * @return {undefined}
     */
    _processDirectPayment: function (code, providerId, processingValues) {
        if (code !== 'neatworldpayvt') {
            this._super(...arguments);
            return;
        }
        if(!processingValues.transaction_key) {
            alert("Worldpay integration is not active. Please update the activation code.");
            return;
        }
        debugger;
        this._enableButton()
        $('body').unblock();
        const popup = document.querySelector('#neatworldpayvt_popup');
        if (popup) {
            popup.style.display = 'block';
            this._populateVirtualTerminalContainer(processingValues);
        }
    },
    
    /**
     * Show the virtual terminal popup with transaction reference.
     *
     * For a provider to redefine the processing of the payment by token flow, it must override
     * this method.
     *
     * @private
     * @param {string} provider_code - The code of the token's provider
     * @param {number} tokenId - The id of the token handling the transaction
     * @param {object} processingValues - The processing values of the transaction
     * @return {undefined}
     */
    _processTokenPayment: function (code, tokenId, processingValues) {
        if (code !== 'neatworldpayvt') {
            this._super(...arguments);
            return;
        }
        if(!processingValues.transaction_key) {
            alert("Worldpay integration is not active. Please update the activation code.");
            return;
        }
        
        $('body').unblock();
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
    _populateVirtualTerminalContainer: function (processingValues) {
        const container = document.querySelector('#neatworldpayvt-container');
        if (!container) return;

        const transactionReference = processingValues.transaction_reference || 'N/A';
        
        // Check if transaction reference ends with dash and number (e.g., "originalref-1")
        const isIterationReference = /-.+$/.test(transactionReference);
        
        container.innerHTML = `
            <div style="text-align: center; padding: 20px;">
                <h3 style="margin-bottom: 20px; color: #334;">Virtual Terminal Payment</h3>
                
                <div style="background: #f8f9fa; border: 1px solid #dee2e6; border-radius: 8px; padding: 20px; margin-bottom: 20px;">
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

}

checkoutForm.include(neatWorldpayvtMixin);
manageForm.include(neatWorldpayvtMixin);