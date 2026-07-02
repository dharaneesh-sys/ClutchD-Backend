import logging

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/safety", tags=["safety"])

_PRIVACY_POLICY_CONTENT = """
# Privacy Policy

Last updated: July 2026

## Information We Collect

We collect information you provide directly to us, including:
- Account registration information (name, email, phone number)
- Profile information and preferences
- Service request details and location data
- Payment information (processed securely through third-party providers)

## How We Use Your Information

We use the information we collect to:
- Provide, maintain, and improve our services
- Process transactions and send related communications
- Connect you with service providers
- Send technical notices, updates, and support messages
- Respond to your comments and questions

## Data Sharing

We do not sell your personal information. We may share data with:
- Service providers to fulfill your service requests
- Payment processors to complete transactions
- Legal authorities when required by law

## Data Security

We implement industry-standard security measures including encryption,
secure servers, and regular security audits to protect your data.

## Your Rights

You have the right to access, update, or delete your personal information
at any time through your account settings.
"""

_TERMS_CONDITIONS_CONTENT = """
# Terms & Conditions

Last updated: July 2026

## Acceptance of Terms

By using ClutchD, you agree to these terms and conditions. If you do not
agree, please do not use our services.

## Service Description

ClutchD connects customers with automotive service providers including
mechanics and garages. We facilitate the booking and payment process but
are not responsible for the quality of services provided by third parties.

## User Responsibilities

- Provide accurate information when creating your account
- Use the platform in compliance with all applicable laws
- Not engage in fraudulent or misleading activities
- Not misuse the platform or interfere with its operation

## Payment Terms

- All payments are processed securely through our payment partners
- Service fees are clearly displayed before booking confirmation
- Refunds are handled according to our refund policy

## Limitation of Liability

ClutchD is not liable for any damages arising from the use of our services,
including but not limited to direct, indirect, incidental, or consequential damages.

## Termination

We reserve the right to suspend or terminate accounts that violate these
terms or engage in prohibited activities.
"""

_DATA_PROTECTION_CONTENT = """
# Data Protection

Last updated: July 2026

## Our Commitment

ClutchD is committed to protecting your personal data in accordance with
applicable data protection laws and regulations.

## Data Collection Principles

- We collect only the data necessary to provide our services
- We obtain your consent before collecting sensitive information
- We provide clear notice about how your data will be used

## Data Retention

We retain your personal data only as long as necessary to:
- Provide you with our services
- Comply with legal obligations
- Resolve disputes
- Enforce our agreements

## Data Transfers

When we transfer data across borders, we ensure appropriate safeguards
are in place to protect your information.

## Data Breach Response

In the event of a data breach, we will:
- Notify affected users within 72 hours
- Take immediate steps to contain the breach
- Cooperate with regulatory authorities

## Contact Us

For data protection inquiries, please contact our Data Protection Officer
at privacy@clutchd.com
"""


@router.get("/privacy-policy")
async def privacy_policy():
    return {
        "title": "Privacy Policy",
        "content": _PRIVACY_POLICY_CONTENT.strip(),
    }


@router.get("/terms-conditions")
async def terms_conditions():
    return {
        "title": "Terms & Conditions",
        "content": _TERMS_CONDITIONS_CONTENT.strip(),
    }


@router.get("/terms")
async def terms_short():
    """Alias for /terms-conditions so frontend's /safety/terms call works."""
    return await terms_conditions()


@router.get("/data-protection")
async def data_protection():
    return {
        "title": "Data Protection",
        "content": _DATA_PROTECTION_CONTENT.strip(),
    }
