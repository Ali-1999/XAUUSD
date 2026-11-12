import unittest

import pandas as pd

from backtest.run import eligible_signals, execution_cost


class ReplayTests(unittest.TestCase):
    def test_signal_uses_next_observed_close_within_two_minutes(self):
        index = pd.to_datetime([600, 660, 780, 960, 1020], unit="s", utc=True)
        signals = [{"type": "retest", "time": 600}, {"type": "retest", "time": 780},
                   {"type": "retest", "time": 960}]
        self.assertEqual(eligible_signals(index, signals), {660: signals[0]})
        self.assertEqual(execution_cost(-2, 4000), 0.75)
