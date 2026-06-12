"""
SorinFlow Divar Scraper - Proxies API Routes
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import datetime
import httpx

from app.database import get_db
from app.models.proxy import Proxy
from app.schemas import ProxyCreate, ProxyResponse, ProxyList

router = APIRouter()


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
    """Test proxy connectivity"""
    result = await db.execute(
        select(Proxy).where(Proxy.id == proxy_id)
    )
    proxy = result.scalar_one_or_none()
    
    if not proxy:
        raise HTTPException(status_code=404, detail="Proxy not found")
    
    # Test proxy
    test_url = "https://divar.ir"
    start_time = datetime.now()
    
    try:
        async with httpx.AsyncClient(
            proxy=proxy.url,
            timeout=30.0
        ) as client:
            response = await client.get(test_url)
            elapsed = (datetime.now() - start_time).total_seconds()
            
            if response.status_code == 200:
                proxy.is_working = True
                proxy.success_count += 1
                proxy.avg_response_time = elapsed
                proxy.last_checked = datetime.now()
                await db.commit()
                
                return {
                    "success": True,
                    "response_time": elapsed,
                    "status_code": response.status_code
                }
            else:
                proxy.is_working = False
                proxy.fail_count += 1
                proxy.last_checked = datetime.now()
                await db.commit()
                
                return {
                    "success": False,
                    "status_code": response.status_code,
                    "message": "Proxy returned non-200 status"
                }
                
    except Exception as e:
        proxy.is_working = False
        proxy.fail_count += 1
        proxy.last_checked = datetime.now()
        await db.commit()
        
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/test-all")
async def test_all_proxies(
    db: AsyncSession = Depends(get_db)
):
    """Test all active proxies"""
    result = await db.execute(
        select(Proxy).where(Proxy.is_active == True)
    )
    proxies = result.scalars().all()
    
    results = []
    
    for proxy in proxies:
        test_url = "https://divar.ir"
        start_time = datetime.now()
        
        try:
            async with httpx.AsyncClient(
                proxy=proxy.url,
                timeout=30.0
            ) as client:
                response = await client.get(test_url)
                elapsed = (datetime.now() - start_time).total_seconds()
                
                if response.status_code == 200:
                    proxy.is_working = True
                    proxy.success_count += 1
                    proxy.avg_response_time = elapsed
                    results.append({
                        "proxy_id": proxy.id,
                        "address": f"{proxy.address}:{proxy.port}",
                        "success": True,
                        "response_time": elapsed
                    })
                else:
                    proxy.is_working = False
                    proxy.fail_count += 1
                    results.append({
                        "proxy_id": proxy.id,
                        "address": f"{proxy.address}:{proxy.port}",
                        "success": False,
                        "status_code": response.status_code
                    })
                    
        except Exception as e:
            proxy.is_working = False
            proxy.fail_count += 1
            results.append({
                "proxy_id": proxy.id,
                "address": f"{proxy.address}:{proxy.port}",
                "success": False,
                "error": str(e)
            })
        
        proxy.last_checked = datetime.now()
    
    await db.commit()
    
    working = sum(1 for r in results if r["success"])
    
    return {
        "total": len(results),
        "working": working,
        "failed": len(results) - working,
        "results": results
    }


from pydantic import BaseModel

class ProxyImportRequest(BaseModel):
    proxy_list: str


@router.post("/import")
async def import_proxies(
    request: ProxyImportRequest,
    db: AsyncSession = Depends(get_db)
):
    """Import proxies from a list (format: ip:port or ip:port:user:pass)"""
    
    lines = request.proxy_list.strip().split("\n")
    imported = 0
    skipped = 0
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
        
        parts = line.split(":")
        
        if len(parts) >= 2:
            try:
                address = parts[0]
                port = int(parts[1])
                username = parts[2] if len(parts) > 2 else None
                password = parts[3] if len(parts) > 3 else None
                
                # Check if exists
                result = await db.execute(
                    select(Proxy).where(
                        Proxy.address == address,
                        Proxy.port == port
                    )
                )
                
                if result.scalar_one_or_none():
                    skipped += 1
                    continue
                
                proxy = Proxy(
                    address=address,
                    port=port,
                    username=username,
                    password=password
                )
                db.add(proxy)
                imported += 1
                
            except ValueError:
                skipped += 1
                continue
    
    await db.commit()
    
    return {
        "imported": imported,
        "skipped": skipped,
        "message": f"Imported {imported} proxies, skipped {skipped}"
    }
