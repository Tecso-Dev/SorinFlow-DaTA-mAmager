"""
Image downloader for Divar property listings.
"""
import asyncio
from pathlib import Path
from typing import List

import httpx
from loguru import logger


async def download_property_images(
    images: List[str],
    divar_id: str,
    images_dir: Path,
) -> List[str]:
    """Download images to <images_dir>/<divar_id>/ and return local file paths."""
    local_paths: List[str] = []
    try:
        property_dir = images_dir / divar_id
        property_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient() as client:
            for i, url in enumerate(images):
                try:
                    response = await client.get(url, timeout=30)
                    if response.status_code == 200:
                        ext = 'webp' if 'webp' in url else 'jpg'
                        filepath = property_dir / f"img_{i + 1}.{ext}"
                        filepath.write_bytes(response.content)
                        local_paths.append(str(filepath))
                        logger.debug(f"Downloaded image {i + 1} for {divar_id}")
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.warning(f"Failed to download image {i + 1} for {divar_id}: {e}")
    except Exception as e:
        logger.error(f"Failed to download images for {divar_id}: {e}")
    return local_paths
