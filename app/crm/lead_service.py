"""
SorinFlow CRM — Lead service
Converts scraped properties into CRM leads.
"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from loguru import logger

from app.models.lead import Lead
from app.models.property import Property


async def create_lead_from_property(db: AsyncSession, prop: Property) -> Lead | None:
    """Create a CRM lead from a newly scraped property.
    Returns None if a lead for this property already exists.
    """
    try:
        existing = await db.execute(
            select(Lead).where(Lead.property_id == prop.id)
        )
        if existing.scalar_one_or_none():
            logger.debug(f"Lead already exists for property {prop.id}")
            return None

        price = prop.total_price or prop.rent_price or prop.price

        lead = Lead(
            property_id=prop.id,
            phone_number=prop.phone_number,
            seller_name=prop.seller_name,
            city_name=prop.city_name,
            category_name=prop.category_name,
            listing_type=prop.listing_type,
            price=price,
            area=prop.area,
            property_url=prop.url,
            property_title=prop.title,
            status="new",
        )
        db.add(lead)
        await db.commit()
        await db.refresh(lead)
        logger.info(f"CRM lead created: id={lead.id} property={prop.id} phone={prop.phone_number}")
        return lead

    except Exception as e:
        logger.error(f"Failed to create CRM lead for property {prop.id}: {e}")
        await db.rollback()
        return None
