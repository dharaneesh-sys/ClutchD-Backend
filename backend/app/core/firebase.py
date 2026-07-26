import json
import logging

import firebase_admin
from firebase_admin import credentials

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_firebase_app: firebase_admin.App | None = None


def init_firebase() -> None:
    """Initialize Firebase Admin SDK from the FIREBASE_SERVICE_ACCOUNT env var.
    
    If the env var is not set, log a warning and skip initialization.
    Must be idempotent — safe to call multiple times.
    """
    global _firebase_app
    
    # Already initialized
    if firebase_admin._apps:
        _firebase_app = list(firebase_admin._apps.values())[0]
        return
    
    settings = get_settings()
    if not settings.fcm_service_account:
        logger.warning("FIREBASE_SERVICE_ACCOUNT not set — push notifications disabled")
        return
    
    try:
        cred_dict = json.loads(settings.fcm_service_account)
        # Fix escaped newlines in private_key (common env var mangling)
        if "private_key" in cred_dict:
            cred_dict["private_key"] = cred_dict["private_key"].replace("\\n", "\n")
        
        cred = credentials.Certificate(cred_dict)
        _firebase_app = firebase_admin.initialize_app(cred)
        logger.info("Firebase Admin SDK initialized successfully")
    except Exception as e:
        logger.error("Failed to initialize Firebase Admin SDK: %s", e)
        _firebase_app = None


def get_firebase_app() -> firebase_admin.App | None:
    """Return the initialized Firebase app, or None if not configured."""
    return _firebase_app
