"""
SorinFlow Divar Scraper - Proxies API Routes
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
from loguru import logger
import httpx
from pydantic import BaseModel
from typing import Optional

from app.database import get_db
from app.models.proxy import Proxy
from app.schemas import ProxyCreate, ProxyResponse, ProxyList

router = APIRouter()


@router.get("", response_model=ProxyList)
@router.get("/", response_model=ProxyList)
async def get_proxies(
    active_only: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """Get list of proxies"""
    query = select(Proxy)
    
    if active_only:
        query = query.where(Proxy.is_active == True)
    
    query = query.order_by(Proxy.success_count.desc())
    
    result = await db.execute(query)
    proxies = result.scalars().all()
    
    return ProxyList(
        items=[ProxyResponse.model_validate(p) for p in proxies],
        total=len(proxies)
    )


@router.post("", response_model=ProxyResponse)
@router.post("/", response_model=ProxyResponse)
async def create_proxy(
    proxy_data: ProxyCreate,
    db: AsyncSession = Depends(get_db)
):
    """Add a new proxy"""
    
    # Check if proxy already exists
    result = await db.execute(
        select(Proxy).where(
            Proxy.address == proxy_data.address,
            Proxy.port == proxy_data.port
        )
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        raise HTTPException(status_code=400, detail="Proxy already exists")
    
    proxy = Proxy(**proxy_data.model_dump())
    db.add(proxy)
    await db.commit()
    await db.refresh(proxy)
    
    return ProxyResponse.model_validate(proxy)


@router.get("/{proxy_id}", response_model=ProxyResponse)
async def get_proxy(
    proxy_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Get proxy by ID"""
    result = await db.execute(
        select(Proxy).where(Proxy.id == proxy_id)
    )
    proxy = result.scalar_one_or_none()
    
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    
    return ProxyResponse.model_validate(proxy)


@router.delete("/{proxy_id}")
async def delete_proxy(
    proxy_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a proxy"""
    result = await db.execute(
        select(Proxy).where(Proxy.id == proxy_id)
    )
    proxy = result.scalar_one_or_none()
    
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    
    await db.delete(proxy)
    await db.commit()
    
    return {"success": True, "message": "Proxy deleted"}


@router.delete("")
@router.delete("/")
async def delete_all_proxies(
    confirm_count: int,
    db: AsyncSession = Depends(get_db)
):
    """Remove every stored proxy.

    Guarded by confirm_count exactly as the broadcast endpoints are: the browser
    sends back the number it displayed, and if the table has changed since it
    was drawn the delete is refused rather than removing rows the person never
    saw. Without that, a stale tab is a way to wipe a list somebody else was
    still adding to.

    Declared before /{proxy_id} would be a concern in a path-matching router,
    but these are distinct paths — "" and "/" cannot collide with an int id.

    Safe for scraping: _get_working_proxy returns None when the table is empty
    and get_context_options simply omits the proxy, so the scraper falls back
    to a direct connection rather than failing.
    """
    rows = (await db.execute(select(Proxy))).scalars().all()
    if confirm_count != len(rows):
        raise HTTPException(
            status_code=409,
            detail=f"تعداد پروکسی‌ها تغییر کرده است ({len(rows)} مورد) — صفحه را تازه کنید")

    for r in rows:
        await db.delete(r)
    await db.commit()

    logger.info(f"[proxies] {len(rows)} proxy(ies) deleted in bulk")
    return {"success": True, "deleted": len(rows)}


@router.post("/{proxy_id}/toggle")
async def toggle_proxy(
    proxy_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Toggle proxy active status"""
    result = await db.execute(
        select(Proxy).where(Proxy.id == proxy_id)
    )
    proxy = result.scalar_one_or_none()
    
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    
    proxy.is_active = not proxy.is_active
    await db.commit()
    
    return {
        "success": True,
        "is_active": proxy.is_active,
        "message": f"Proxy {'activated' if proxy.is_active else 'deactivated'}"
    }


@router.post("/{proxy_id}/test")
async def test_proxy(
    proxy_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Test one proxy against Divar and learn where it exits."""
    from app.services import proxy_pool
    result = await db.execute(select(Proxy).where(Proxy.id == proxy_id))
    proxy = result.scalar_one_or_none()
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    outcome = await proxy_pool.probe(proxy)
    await db.commit()
    return outcome


@router.post("/test-all")
async def test_all_proxies(
    db: AsyncSession = Depends(get_db)
):
    """Test every active proxy. The same probe the daily loop runs."""
    from app.services import proxy_pool
    result = await db.execute(select(Proxy).where(Proxy.is_active == True))  # noqa: E712
    proxies = result.scalars().all()
    results = [await proxy_pool.probe(p) for p in proxies]
    await db.commit()
    working = sum(1 for r in results if r.get("success"))
    iranian = sum(1 for r in results if r.get("success") and r.get("exit_country") == "IR")
    return {"total": len(results), "working": working, "iranian": iranian, "results": results}


class ProxyImportRequest(BaseModel):
    proxy_list: Optional[str] = None
    url: Optional[str] = None
    # Probe what was imported right away, so the panel shows Divar's verdict
    # without a second click.
    test: bool = True


@router.post("/import")
async def import_proxies(
    request: ProxyImportRequest,
    db: AsyncSession = Depends(get_db)
):
    """Import proxies from a pasted list, or from a URL that serves one.

    Accepts ip:port, ip:port:user:pass, or scheme://[user:pass@]host:port.
    Imported rows start untested (is_working=False): nothing reaches the
    scraper until the Divar probe has passed it. That gate is the whole
    defence against a list of dead or foreign exits — and a free list is
    mostly both.
    """
    from app.services import proxy_pool

    text = request.proxy_list or ""
    if request.url:
        try:
            text = await proxy_pool.fetch_list(request.url)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"could not fetch the list: {type(e).__name__}")

    imported = skipped = 0
    for entry in proxy_pool.parse_list(text):
        exists = (await db.execute(
            select(Proxy).where(Proxy.address == entry["address"], Proxy.port == entry["port"])
        )).scalar_one_or_none()
        if exists:
            skipped += 1
            continue
        db.add(Proxy(**entry, is_working=False))
        imported += 1
    await db.commit()

    tested = None
    if request.test and imported:
        tested = await proxy_pool.refresh_all()
    return {"imported": imported, "skipped": skipped, "tested": tested}
