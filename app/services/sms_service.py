"""
SorinFlow CRM — SMS service
Supports Kavenegar and Melipayamak providers.
"""
import httpx
from loguru import logger
from app.config import get_settings

settings = get_settings()


async def send_sms(to_number: str, message: str, provider: str = "kavenegar") -> dict:
    """
    Send an SMS via the chosen provider.
    Returns {"success": bool, "provider": str, "response": str}
    """
    if provider == "melipayamak":
        return await _send_melipayamak(to_number, message)
    return await _send_kavenegar(to_number, message)


async def _send_kavenegar(to_number: str, message: str) -> dict:
    api_key = settings.kavenegar_api_key
    sender = settings.kavenegar_sender

    if not api_key:
        return {"success": False, "provider": "kavenegar", "response": "KAVENEGAR_API_KEY not set"}

    url = f"https://api.kavenegar.com/v1/{api_key}/sms/send.json"
    payload = {"receptor": to_number, "message": message}
    if sender:
        payload["sender"] = sender

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, data=payload)
        body = resp.text
        success = resp.status_code == 200
        logger.info(f"Kavenegar SMS → {to_number}: status={resp.status_code}")
        return {"success": success, "provider": "kavenegar", "response": body}
    except Exception as e:
        logger.error(f"Kavenegar SMS error: {e}")
        return {"success": False, "provider": "kavenegar", "response": str(e)}


async def _send_melipayamak(to_number: str, message: str) -> dict:
    api_key = settings.melipayamak_api_key
    from_number = settings.melipayamak_from

    if not api_key:
        return {"success": False, "provider": "melipayamak", "response": "MELIPAYAMAK_API_KEY not set"}

    url = f"https://api.melipayamak.com/api/send/simple/{api_key}"
    payload = {"to": to_number, "from": from_number, "text": message}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
        body = resp.text
        success = resp.status_code == 200
        logger.info(f"Melipayamak SMS → {to_number}: status={resp.status_code}")
        return {"success": success, "provider": "melipayamak", "response": body}
    except Exception as e:
        logger.error(f"Melipayamak SMS error: {e}")
        return {"success": False, "provider": "melipayamak", "response": str(e)}
