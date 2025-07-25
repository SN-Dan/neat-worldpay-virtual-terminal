{
    'name': 'Payment Provider: Worldpay Virtual Terminal',
    'version': '2.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': "Worldpay Official Integration for Virtual Terminal Payments.",
    'description': " ",
    'author': 'SNS Software',
    'maintainer': 'SNS Software',
    'website': 'https://www.sns-software.com',
    'depends': ['payment'],
    'images': ['static/description/main.gif'],
    'data': [
        'views/payment_provider_views.xml',
        'views/payment_form_templates.xml',

        'data/payment_provider_data.xml'
    ],
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'assets': {
        'web.assets_frontend': [
            'payment_neatworldpayvt/static/src/js/payment_form.js'
        ],
        'web.assets_backend': [
            'payment_neatworldpayvt/static/src/css/neatworldpay.css',
            'payment_neatworldpayvt/static/src/js/neatworldpay.js',
        ],
    },
    'license': 'LGPL-3',
}