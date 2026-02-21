import os


DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg://postgres:postgres@localhost:5432/voiceagent",
)

ENV = os.getenv("ENV", "dev").lower()
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.getenv(
    "GOOGLE_REDIRECT_URI",
    "https://<ngrok-host>/v1/integrations/google/oauth/callback",
)
GOOGLE_OAUTH_STATE_SECRET = os.getenv("GOOGLE_OAUTH_STATE_SECRET", "")
