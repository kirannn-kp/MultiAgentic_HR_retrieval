"""Keep the complete test suite independent from the developer's live .env."""
import os

os.environ["MOCK_MODE"] = "true"
os.environ["MOCK_LATENCY_SEC"] = "0"
os.environ["PROJECT_ENDPOINT"] = ""
os.environ["REGIONAL_RETRIEVAL_ENABLED"] = "false"
