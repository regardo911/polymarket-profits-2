"""Shared test plumbing: put polymarket-bots/ on the path, refuse every real
network connection, and run each test in a scratch folder so no trade log or
agent log lands in the repo. Every market, book and wallet below is made up."""
import os
import socket
import sys
import tempfile
import unittest

BOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "polymarket-bots")
if BOTS not in sys.path:
    sys.path.insert(0, BOTS)


class NetworkBlocked(RuntimeError):
    pass


def _refuse(*args, **kwargs):
    raise NetworkBlocked(f"test tried to open a network connection: {args[:1]}")


socket.socket.connect = _refuse
socket.create_connection = _refuse


class Response:
    """Stand-in for requests.Response."""

    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status
        self.ok = status < 400

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self.ok:
            import requests
            raise requests.HTTPError(f"{self.status_code} for made-up url")


def market(question="Will Made-Up Coin close above $10?", yes="YES1", no="NO1", **extra):
    m = {"question": question, "clobTokenIds": f'["{yes}", "{no}"]', "acceptingOrders": True,
         "closed": False, "feesEnabled": True, "feeSchedule": {"rate": 0.07},
         "orderPriceMinTickSize": 0.01, "negRisk": False, "endDate": "2099-01-01T00:00:00Z"}
    m.update(extra)
    return m


class InScratchDir(unittest.TestCase):
    def setUp(self):
        self._old = os.getcwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old)
        self._tmp.cleanup()
