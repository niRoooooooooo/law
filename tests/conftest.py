
import os

# Set safe test values BEFORE app.main is imported, because
# app.main builds the module-level app at import time.
# load_dotenv() never overrides variables that are already set,
# so the real .env does not affect these tests.
os.environ["TOR_LAW_ADMIN_ENABLED"] = "0"
os.environ["TOR_LAW_ADMIN_KEY"] = "test-admin-key-not-real-0123456789abcdef"
os.environ["TOR_LAW_ADMIN_SESSION_MINUTES"] = "30"
os.environ["TOR_LAW_ADMIN_COOKIE_SECURE"] = "1"
os.environ["TOR_LAW_DEBUG"] = "0"
