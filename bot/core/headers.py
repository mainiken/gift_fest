from typing import Dict
from bot.core.agents import generate_random_user_agent

def get_agentx_headers(token: str) -> dict:
    return {
        "Accept-Language": "ru-RU,ru;q=0.9,en-NL;q=0.8,en-US;q=0.7,en;q=0.6",
        "Connection": "keep-alive",
        "If-None-Match": 'W/"2ef9-fZ/C6gM+FPcmIYJV+v8NbPFChG0"',
        "Origin": "https://app.agentx.pw",
        "Referer": "https://app.agentx.pw/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
        "accept": "application/json",
        "authorization": f"Bearer {token}",
    }


def get_tonminefarm_headers() -> dict:
    """Заголовки для API tonminefarm.com"""
    return {
        "Accept": "*/*",
        "Accept-Language": "ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7",
        "Connection": "keep-alive",
        "Content-Type": "application/json; charset=UTF-8",
        "Origin": "https://app.tonminefarm.com",
        "Referer": "https://app.tonminefarm.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-site",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0",
        "sec-ch-ua": '"Chromium";v="139", "Microsoft Edge WebView2";v="139", "Microsoft Edge";v="139", "Not;A=Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def get_giftfest_headers(init_data: str = "") -> dict:
    """Заголовки для API gift.stepcdn.space"""
    headers = {
        "accept": "*/*",
        "accept-language": "ru,en;q=0.9,en-GB;q=0.8,en-US;q=0.7",
        "content-type": "application/json",
        "origin": "https://gift-static.stepcdn.space",
        "priority": "u=1, i",
        "referer": "https://gift-static.stepcdn.space/",
        "sec-ch-ua": '"Microsoft Edge";v="142", "Microsoft Edge WebView2";v="142", "Chromium";v="142", "Not_A Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-site",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
        "x-platform": "tdesktop",
        "x-service-name": "gift",
        "x-timezone-offset": "-180",
    }
    
    if init_data:
        headers["authorization"] = f"tma {init_data}"
    
    return headers

