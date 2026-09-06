### PAYMENT PLAN
** Transfer API **: https://api.flutterwave.com/v3/charges?type=bank_transfer
1. Required Body Params:
    {
        "amount": 100,
        "email": tenant email,
        "currency": "NGN",
        "tx_ref": "MC-MC-1585230ew9v5050e8",
    }
    

2. Response:
    {
    "status": "success",
    "message": "Charge initiated",
    "meta": {
        "authorization": {
        "transfer_reference": "ZZSS4548178111081675127069",
        "transfer_account": "9755152912",
        "transfer_bank": "Flutterwave MFB",
        "account_expiration": "2020-03-27 09:53:39",
        "transfer_note": "Please make a bank transfer to Yemi Desola",
        "transfer_amount": 1500,
        "mode": "banktransfer"
        }
    }
    }

** Card Payment **: https://api.flutterwave.com/v3/payments
1. Required body params:
    {
        "amount": 100,
        "currency": "NGN",
        "tx_ref": "MC-MC-1585230ew9v5050e8",
         "customer": {
            "email": "user@example.com",
            },
    }

2. Rsponse:
    {
    "status": "success",
    "message": "Hosted Link",
    "data": {
        "link": "https://checkout.flutterwave.com/v3/hosted/pay/flwlnk-01j4dc4maegqf82pfqg4chw46b"
        }
    }


## TODO
We need a way to call these two API and put their response in one response for our API.
The link should be put in a qrcode(for customer to scan to pay)
So our API return
{
    qrcode_link: ...,
    transfer_data: {
        "transfer_reference": "ZZSS4548178111081675127069",
        "transfer_account": "9755152912",
        "transfer_bank": "Flutterwave MFB",
        "account_expiration": "2020-03-27 09:53:39",
        "transfer_note": "Please make a bank transfer to Yemi Desola",
        "transfer_amount": 1500,
        "mode": "banktransfer"
    }
}

so in our UI we render QRCode and transfer data(customer can scan or transfer)