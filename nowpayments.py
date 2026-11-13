"""NOWPayments API wrapper for crypto payments."""
import logging
import requests
from typing import Optional

log = logging.getLogger(__name__)

class NowPayments:
    BASE_URL = "https://api.nowpayments.io/v1"
    SANDBOX_URL = "https://api-sandbox.nowpayments.io/v1"
    
    def __init__(self, api_key: str, sandbox: bool = False):
        self.api_key = api_key
        self.base_url = self.SANDBOX_URL if sandbox else self.BASE_URL
        self.headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }
    
    def create_payment(self, price_usd: float, pay_currency: str, order_id: str, description: str = "") -> dict:
        data = {
            "price_amount": price_usd,
            "price_currency": "usd",
            "pay_currency": pay_currency.lower(),
            "order_id": order_id,
            "order_description": description,
        }
        try:
            r = requests.post(f"{self.base_url}/payment", json=data, headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            log.error("NOWPayments create_payment failed: %s", e)
            raise
    
    def get_payment_status(self, payment_id: str) -> dict:
        try:
            r = requests.get(f"{self.base_url}/payment/{payment_id}", headers=self.headers, timeout=10)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException as e:
            log.error("NOWPayments get_payment_status failed: %s", e)
            raise
    
    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/status", headers=self.headers, timeout=5)
            return r.status_code == 200
        except:
            return False
