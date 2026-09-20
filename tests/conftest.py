"""Pytest configuration for the EstimateGuard test suite.

Every test process stamps observation rows as source='test' automatically,
so test runs can never pollute the production observation corpus -- no
per-call passing required.
"""

import os

os.environ["ESTIMATEGUARD_OBSERVATION_SOURCE"] = "test"
