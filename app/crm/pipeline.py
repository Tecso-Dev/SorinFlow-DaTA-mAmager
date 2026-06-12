"""
SorinFlow CRM — Main pipeline
Called by the scraper after every new property is saved.
"""
from datetime import datetime
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.property import Property
from app.crm.lead_service import create_lead_from_property
from app.crm import notification as notifier


async def process_new_property(db: AsyncSession, prop: Property) -> None:
    """
    Entry point: receive a freshly-saved Property and:
    1. Create a CRM lead
    2. Send notifications via configured channels
    3. Mark the lead as notified
    """
    try:
        lead = await create_lead_from_property(db, prop)
        if not lead:
            return

        channel = await notifier.notify(prop, lead)

        lead.notified = channel != "none"
        lead.notified_at = datetime.now() if lead.notified else None
        lead.notification_channel = channel
        await db.commit()

        logger.info(
            f"CRM pipeline done — lead #{lead.id} | notified={lead.notified} | channel={channel}"
        )

    except Exception as e:
        logger.error(f"CRM pipeline error for property {prop.id}: {e}")
