"""ICC trading bot — a mechanical interpretation of the SCI 'ICC' price-action method.

ICC = Indication -> Correction -> Continuation. This package turns that
discretionary method into explicit, testable rules and drives a broker adapter
(Webull for equities, Coinbase for crypto).

SAFETY: everything defaults to dry-run (no orders placed). Live trading requires
explicit configuration and real API credentials. This is not financial advice
and can lose money.
"""

__version__ = "0.1.0"
