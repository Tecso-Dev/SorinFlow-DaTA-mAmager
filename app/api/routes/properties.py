"""
SorinFlow Divar Scraper - Properties API Routes
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, and_, or_
from typing import Optional, List
from datetime import datetime, timedelta

from app.database import get_db
from app.models.property import Property, City, Category
from app.models.user import User
from app.auth.dependencies import get_current_user_optional
from app.schemas import (
    PropertyResponse,
    PropertyListResponse,
    PropertyFilter,
    PropertyUpdate
)

router = APIRouter(redirect_slashes=False)


@router.get("", response_model=PropertyListResponse)
@router.get("/", response_model=PropertyListResponse)
async def get_properties(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    city: Optional[str] = None,
    category: Optional[str] = None,
    listing_type: Optional[str] = None,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    # Separate filters for rent listings
    min_deposit: Optional[int] = None,
    max_deposit: Optional[int] = None,
    min_rent_price: Optional[int] = None,
    max_rent_price: Optional[int] = None,
    min_area: Optional[int] = None,
    max_area: Optional[int] = None,
    min_rooms: Optional[int] = None,
    max_rooms: Optional[int] = None,
    has_phone: Optional[bool] = None,
    search: Optional[str] = None,
    owner_phone: Optional[str] = None,
    sort_by: str = "scraped_at",
    sort_order: str = "desc",
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get paginated list of properties with filters"""

    # Build query
    query = select(Property).where(Property.is_active == True)

    # All roles see every scraped property; owner_phone remains available
    # as an explicit opt-in filter only
    if owner_phone:
        query = query.where(Property.owner_phone == owner_phone)

    # Apply filters
    if city:
        query = query.where(Property.city_name.ilike(f"%{city}%"))
    
    if category:
        query = query.where(Property.category_name.ilike(f"%{category}%"))
    
    if listing_type:
        query = query.where(Property.listing_type == listing_type)
    
    if min_price is not None:
        query = query.where(
            or_(
                Property.total_price >= min_price,
                Property.rent_price >= min_price
            )
        )
    
    if max_price is not None:
        query = query.where(
            or_(
                Property.total_price <= max_price,
                Property.rent_price <= max_price
            )
        )

    # Deposit range (only applies to rent listings but safe in general)
    if min_deposit is not None:
        query = query.where(Property.deposit >= min_deposit)

    if max_deposit is not None:
        query = query.where(Property.deposit <= max_deposit)

    # Monthly rent range
    if min_rent_price is not None:
        query = query.where(Property.rent_price >= min_rent_price)

    if max_rent_price is not None:
        query = query.where(Property.rent_price <= max_rent_price)
    
    if min_area is not None:
        query = query.where(Property.area >= min_area)
    
    if max_area is not None:
        query = query.where(Property.area <= max_area)
    
    if min_rooms is not None:
        query = query.where(Property.rooms >= min_rooms)
    
    if max_rooms is not None:
        query = query.where(Property.rooms <= max_rooms)
    
    if has_phone is not None:
        if has_phone:
            query = query.where(Property.phone_number.isnot(None))
        else:
            query = query.where(Property.phone_number.is_(None))
    
    if search:
        query = query.where(
            or_(
                Property.title.ilike(f"%{search}%"),
                Property.description.ilike(f"%{search}%"),
                Property.district.ilike(f"%{search}%"),
                Property.neighborhood.ilike(f"%{search}%")
            )
        )
    
    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    # Apply sorting
    sort_column = getattr(Property, sort_by, Property.scraped_at)
    if sort_order == "desc":
        query = query.order_by(sort_column.desc())
    else:
        query = query.order_by(sort_column.asc())
    
    # Apply pagination
    offset = (page - 1) * size
    query = query.offset(offset).limit(size)
    
    result = await db.execute(query)
    properties = result.scalars().all()
    
    return PropertyListResponse(
        items=[PropertyResponse.model_validate(p) for p in properties],
        total=total,
        page=page,
        size=size,
        pages=(total + size - 1) // size
    )


@router.get("/{property_id}", response_model=PropertyResponse)
async def get_property(
    property_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get a single property by ID"""
    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property = result.scalar_one_or_none()
    
    if not property:
        raise HTTPException(status_code=404, detail="Property not found")
    
    return PropertyResponse.model_validate(property)


@router.get("/tag/{tag_number}", response_model=PropertyResponse)
async def get_property_by_tag(
    tag_number: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a property by tag number"""
    result = await db.execute(
        select(Property).where(Property.tag_number == tag_number)
    )
    property = result.scalar_one_or_none()
    
    if not property:
        raise HTTPException(status_code=404, detail="Property not found")
    
    return PropertyResponse.model_validate(property)


@router.get("/divar/{divar_id}", response_model=PropertyResponse)
async def get_property_by_divar_id(
    divar_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a property by Divar ID"""
    result = await db.execute(
        select(Property).where(Property.divar_id == divar_id)
    )
    property = result.scalar_one_or_none()
    
    if not property:
        raise HTTPException(status_code=404, detail="Property not found")
    
    return PropertyResponse.model_validate(property)


@router.patch("/{property_id}", response_model=PropertyResponse)
async def update_property(
    property_id: int,
    updates: PropertyUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a property"""
    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property = result.scalar_one_or_none()
    
    if not property:
        raise HTTPException(status_code=404, detail="Property not found")
    
    update_data = updates.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(property, field, value)
    
    property.updated_at = datetime.now()
    await db.commit()
    await db.refresh(property)
    
    return PropertyResponse.model_validate(property)


@router.delete("/{property_id}")
async def delete_property(
    property_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Soft delete a property"""
    result = await db.execute(
        select(Property).where(Property.id == property_id)
    )
    property = result.scalar_one_or_none()
    
    if not property:
        raise HTTPException(status_code=404, detail="Property not found")
    
    property.is_active = False
    property.updated_at = datetime.now()
    await db.commit()
    
    return {"message": "Property deleted successfully"}


@router.get("/cities/list", response_model=List[dict])
async def get_cities(
    db: AsyncSession = Depends(get_db)
):
    """Get list of available cities"""
    result = await db.execute(
        select(City).where(City.is_active == True).order_by(City.name)
    )
    cities = result.scalars().all()
    
    return [{"id": c.id, "name": c.name, "slug": c.slug, "province": c.province} for c in cities]


@router.get("/categories/list", response_model=List[dict])
async def get_categories(
    db: AsyncSession = Depends(get_db)
):
    """Get list of available categories"""
    result = await db.execute(
        select(Category).where(Category.is_active == True).order_by(Category.name)
    )
    categories = result.scalars().all()
    
    return [{"id": c.id, "name": c.name, "slug": c.slug} for c in categories]


@router.post("/export")
async def export_properties(
    filters: PropertyFilter,
    format: str = Query("json", enum=["json", "csv"]),
    db: AsyncSession = Depends(get_db)
):
    """Export properties with filters"""
    # Build query with filters (similar to get_properties)
    query = select(Property).where(Property.is_active == True)
    
    if filters.city:
        query = query.where(Property.city_name.ilike(f"%{filters.city}%"))
    
    if filters.listing_type:
        query = query.where(Property.listing_type == filters.listing_type)
    
    result = await db.execute(query.limit(10000))  # Limit export
    properties = result.scalars().all()
    
    if format == "csv":
        import csv
        import io

        # Align CSV columns with the main properties table in the dashboard
        # and use Persian headers for a cleaner Excel view.
        output = io.StringIO()
        writer = csv.writer(output)

        # Header row (Persian, matching UI):
        writer.writerow([
            "شناسه",
            "عنوان",
            "شهر",
            "متراژ",
            "اتاق",
            "قیمت فروش",
            "ودیعه",
            "اجاره ماهانه",
            "شماره تماس",
        ])

        for p in properties:
            d = p.to_dict()
            row = [
                d.get("tag_number") or "",
                d.get("title") or "",
                d.get("city_name") or "",
                d.get("area") or "",
                d.get("rooms") or "",
                d.get("price") or "",
                d.get("deposit") or "",
                d.get("rent_price") or "",
                d.get("phone_number") or "",
            ]
            writer.writerow(row)

        return {"data": output.getvalue(), "format": "csv"}
    
    return {"data": [p.to_dict() for p in properties], "format": "json"}


@router.post("/fix-has-images")
async def fix_has_images(db: AsyncSession = Depends(get_db)):
    """Fix has_images flag for all properties where it's inconsistent with actual images"""
    result = await db.execute(select(Property))
    properties = result.scalars().all()
    fixed = 0
    for prop in properties:
        correct = bool(prop.images)
        if prop.has_images != correct:
            prop.has_images = correct
            fixed += 1
    await db.commit()
    return {"fixed": fixed, "total": len(properties)}
