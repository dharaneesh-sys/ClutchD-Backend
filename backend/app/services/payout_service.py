import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import get_settings
from app.models.job import Job
from app.models.payment import Payment

logger = logging.getLogger(__name__)

async def process_payouts(db: AsyncSession, payment: Payment, job: Job):
    """
    Automated Payout Logic:
    Route funds when a payment successfully captures.
    The customer pays `totalAmount`.
    - `serviceAmount` goes to `job.provider_upi_id` (mechanic or garage)
    - the rest (convenience fee + distance fee + cancellation fee + GST) goes to platform UPI `amdevanand206@oksbi`
    """
    if not job.service_amount or not job.total_amount:
        logger.warning(f"Job {job.id} does not have pricing details for payout.")
        return

    provider_amount = job.service_amount
    platform_amount = job.total_amount - job.service_amount

    provider_upi = getattr(job, "provider_upi_id", "NOT_SET")
    platform_upi = get_settings().platform_upi_id

    # Simulate payout logic (e.g. Razorpay Route / UPI Transfer logic)
    logger.info(f"--- TRIGGERING AUTOMATED PAYOUT FOR JOB {job.id} ---")
    logger.info(f"PAYMENT ID: {payment.id}")
    logger.info(f"Routing ₹{provider_amount:.2f} to Provider UPI: {provider_upi}")
    logger.info(f"Routing ₹{platform_amount:.2f} to Platform UPI: {platform_upi}")
    logger.info(f"-------------------------------------------------------")

    # In a real environment, we would use Razopay route API to transfer funds to a linked account or use API integrations like RazorpayX
