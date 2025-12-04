import aiohttp
import asyncio
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import urlencode, unquote, urlparse, parse_qsl, urlunparse
from aiocfscrape import CloudflareScraper
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from random import uniform, randint
from time import time
from datetime import datetime, timezone
import json
import os
import re

from bot.utils.universal_telegram_client import UniversalTelegramClient
from bot.utils.proxy_utils import check_proxy, get_working_proxy
from bot.utils.first_run import check_is_first_run, append_recurring_session
from bot.config import settings
from bot.utils import logger, config_utils, CONFIG_PATH
from bot.exceptions import InvalidSession
from bot.core.headers import get_giftfest_headers


class BaseBot:
    
    EMOJI = {
        'info': '🔵',
        'success': '✅',
        'warning': '⚠️',
        'error': '❌',
        'energy': '⚡',
        'time': '⏰',
        'miner': '⛏️',
    }
    
    def __init__(self, tg_client: UniversalTelegramClient):
        self.tg_client = tg_client
        if hasattr(self.tg_client, 'client'):
            self.tg_client.client.no_updates = True
        self.session_name = tg_client.session_name
        self._http_client: Optional[CloudflareScraper] = None
        self._current_proxy: Optional[str] = None
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._is_first_run: Optional[bool] = None
        self._init_data: Optional[str] = None
        self._current_ref_id: Optional[str] = None
        
        # Загрузка конфигурации сессии
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        if not all(key in session_config for key in ('api', 'user_agent')):
            logger.critical(f"CHECK accounts_config.json as it might be corrupted")
            exit(-1)
            
        # Настройка прокси
        self.proxy = session_config.get('proxy')
        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            self.tg_client.set_proxy(proxy)
            self._current_proxy = self.proxy

    def get_ref_id(self) -> str:
        if self._current_ref_id is None:
            import base64
            from urllib.parse import unquote
            
            session_hash = sum(ord(c) for c in self.session_name)
            remainder = session_hash % 10
            
            if remainder < 6 and settings.REF_ID:
                ref_param = settings.REF_ID
            else:
                ref_param = 'UkM9MDAwMDAwSDVHY0UmUlM9aW52aXRlX2ZyaWVuZA%3D%3D'
            
            ref_param_decoded = unquote(ref_param)
            
            if ref_param_decoded.endswith('=='):
                ref_param_decoded = ref_param_decoded.rstrip('=')
            
            self._current_ref_id = ref_param_decoded
        return self._current_ref_id
    
    def _replace_webapp_version(self, url: str, version: str = "9.0") -> str:
        from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

        parsed = urlparse(url)
        # Заменяем/добавляем в query
        query_params = dict(parse_qsl(parsed.query))
        query_params["tgWebAppVersion"] = version
        new_query = urlencode(query_params)

        # Заменяем/добавляем в fragment (если есть)
        fragment = parsed.fragment
        if "tgWebAppVersion=" in fragment:
            parts = fragment.split("&")
            parts = [
                f"tgWebAppVersion={version}" if p.startswith("tgWebAppVersion=") else p
                for p in parts
            ]
            fragment = "&".join(parts)

        new_url = urlunparse((
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            parsed.params,
            new_query,
            fragment
        ))
        return new_url

    async def get_tg_web_data(self, app_name: str = "giftfest_bot", path: str = "app") -> Tuple[str, Optional[dict]]:
        try:
            webview_url = await self.tg_client.get_app_webview_url(
                app_name,
                path,
                self.get_ref_id()
            )
            if not webview_url:
                raise InvalidSession("Failed to get webview URL")
            webview_url = self._replace_webapp_version(webview_url, "9.0")
            
            if settings.DEBUG_LOGGING:
                logger.debug(f"[{self.session_name}] 🌐 Полученный URL: {webview_url[:100]}...")
                logger.debug(f"[{self.session_name}] Original webview_url: {webview_url}")
            
            hash_index = webview_url.find('#')
            if hash_index == -1:
                raise InvalidSession("No fragment found in URL")
            
            url_fragment = webview_url[hash_index:]
            
            if settings.DEBUG_LOGGING:
                logger.debug(f"[{self.session_name}] 🔗 URL fragment: {url_fragment[:150]}...")
                logger.debug(f"[{self.session_name}] URL fragment: {url_fragment}")
            
            match = re.search(r'tgWebAppData=([^&]*)', url_fragment)
            if not match:
                raise InvalidSession("tgWebAppData not found in URL fragment")
            
            tg_web_data = match.group(1)
            from urllib.parse import unquote
            import base64
            tg_web_data_decoded = unquote(tg_web_data)
            
            start_param_match = re.search(r'(?:start_param|tgWebAppStartParam)=([^&%]*)', tg_web_data_decoded)
            ref_data = None
            if start_param_match:
                start_param = unquote(start_param_match.group(1))
                if settings.DEBUG_LOGGING:
                    logger.debug(f"[{self.session_name}] 🔍 Найден start_param: {start_param}")
                try:
                    padding = '=' * (4 - len(start_param) % 4) if len(start_param) % 4 else ''
                    decoded_param = base64.b64decode(start_param + padding).decode('utf-8')
                    if settings.DEBUG_LOGGING:
                        logger.debug(f"[{self.session_name}] 🔓 Декодирован start_param: {decoded_param}")
                    
                    parts = decoded_param.split('&')
                    ref_code = None
                    ref_source = None
                    for part in parts:
                        if part.startswith('RC='):
                            ref_code = part.split('=', 1)[1]
                        elif part.startswith('RS='):
                            ref_source = part.split('=', 1)[1]
                    
                    if ref_code and ref_source:
                        ref_data = {
                            "referral_code": ref_code,
                            "referral_source": ref_source
                        }
                        if settings.DEBUG_LOGGING:
                            logger.debug(f"[{self.session_name}] ✅ Извлечены реферальные данные: код={ref_code}, источник={ref_source}")
                    else:
                        if settings.DEBUG_LOGGING:
                            logger.debug(f"[{self.session_name}] ⚠️ Не удалось извлечь ref_code или ref_source из: {decoded_param}")
                except Exception as e:
                    logger.error(f"{self.session_name} | ❌ Ошибка декодирования start_param: {e}")
                    if settings.DEBUG_LOGGING:
                        import traceback
                        logger.debug(f"[{self.session_name}] Traceback: {traceback.format_exc()}")
            else:
                if settings.DEBUG_LOGGING:
                    logger.debug(f"[{self.session_name}] ⚠️ start_param не найден в URL")
            
            if settings.DEBUG_LOGGING:
                logger.debug(f"[{self.session_name}] Extracted tgWebAppData: {tg_web_data_decoded}")
            
            return tg_web_data_decoded, ref_data
        except Exception as e:
            logger.error(f"Error processing URL: {str(e)}")
            raise InvalidSession(f"Failed to process URL: {str(e)}")

    async def initialize_session(self) -> bool:
        try:
            self._is_first_run = await check_is_first_run(self.session_name)
            if self._is_first_run:
                logger.info(f"{self.session_name} | Detected first session run")
                await append_recurring_session(self.session_name)
            return True
        except Exception as e:
            logger.error(f"{self.session_name} | Session initialization error: {str(e)}")
            return False

    async def login(self, tg_web_data: str, ref_data: Optional[dict] = None) -> bool:
        """Авторизация в GiftFest через tgWebAppData"""
        try:
            headers = get_giftfest_headers(tg_web_data)
            
            if settings.DEBUG_LOGGING:
                logger.debug(f"[{self.session_name}] Login headers: {headers}")
            
            login_body = {}
            if ref_data:
                login_body = ref_data
                logger.info(f"{self.session_name} | 🎁 Первый запуск с реферальным кодом: {ref_data.get('referral_code')} (источник: {ref_data.get('referral_source')})")
                if settings.DEBUG_LOGGING:
                    logger.debug(f"[{self.session_name}] Login body: {login_body}")
            
            response = await self.make_request(
                method="POST",
                url="https://gift.stepcdn.space/auth/new",
                headers=headers,
                json=login_body
            )
            
            if settings.DEBUG_LOGGING:
                logger.debug(f"[{self.session_name}] Login response: {response}")
            
            if response and response.get("access_token"):
                self._access_token = response.get("access_token")
                self._refresh_token = response.get("refresh_token")
                self._init_data = tg_web_data
                
                my_referral_code = response.get("referral_code")
                if my_referral_code:
                    logger.info(f"{self.session_name} | ✅ Авторизация успешна | Мой реферальный код: {my_referral_code}")
                else:
                    logger.info(f"{self.session_name} | ✅ Авторизация успешна")
                return True
            else:
                logger.error(f"{self.session_name} | Авторизация неуспешна, response: {response}")
                return False
        except Exception as error:
            logger.error(f"{self.session_name} | Ошибка авторизации: {str(error)}")
            return False

    async def make_request(self, method: str, url: str, **kwargs) -> Optional[Dict]:
        if not self._http_client:
            logger.error(f"[{self.session_name}] HTTP client not initialized")
            raise InvalidSession("HTTP client not initialized")
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] make_request: method={method}, url={url}, kwargs={kwargs}")
        for attempt in range(2):
            try:
                async with getattr(self._http_client, method.lower())(url, **kwargs) as response:
                    if settings.DEBUG_LOGGING:
                        logger.debug(f"[{self.session_name}] response.status: {response.status}")
                        try:
                            response_text = await response.text()
                            logger.debug(f"[{self.session_name}] response.text: {response_text}")
                        except Exception as e:
                            logger.debug(f"[{self.session_name}] response.text error: {e}")
                    if response.status == 200:
                        try:
                            return await response.json()
                        except Exception:
                            return {}
                    if response.status in (401, 502, 403, 418):
                        logger.warning(f"[{self.session_name}] Access token expired or server error, пытаюсь re-login...")
                        tg_web_data, _ = await self.get_tg_web_data()
                        relogin = await self.login(tg_web_data)
                        if relogin:
                            logger.info(f"[{self.session_name}] Re-login успешен, повтор запроса...")
                            kwargs_copy = kwargs.copy()
                            if 'headers' in kwargs_copy:
                                kwargs_copy['headers'] = self._get_auth_headers() if hasattr(self, '_get_auth_headers') else kwargs_copy['headers']
                            continue
                        logger.error(f"[{self.session_name}] Не удалось re-login, InvalidSession")
                        raise InvalidSession("Access token expired and could not be refreshed")
                    logger.error(f"[{self.session_name}] Request failed with status {response.status}")
                    return None
            except Exception as e:
                logger.error(f"[{self.session_name}] Request error: {str(e)}")
                if settings.DEBUG_LOGGING:
                    logger.debug(f"[{self.session_name}] Exception in make_request: {e}")
                return None

    async def run(self) -> None:
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] run: start initialize_session")
        if not await self.initialize_session():
            logger.error(f"[{self.session_name}] Failed to initialize session")
            raise InvalidSession("Failed to initialize session")
        random_delay = uniform(1, settings.SESSION_START_DELAY)
        logger.info(f"Bot will start in {int(random_delay)}s")
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] Sleeping for {random_delay} seconds before start")
        await asyncio.sleep(random_delay)
        proxy_conn = {'connector': ProxyConnector.from_url(self._current_proxy)} if self._current_proxy else {}
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] proxy_conn: {proxy_conn}")
        async with CloudflareScraper(timeout=aiohttp.ClientTimeout(60), **proxy_conn) as http_client:
            self._http_client = http_client
            
            session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
            if settings.DEBUG_LOGGING:
                logger.debug(f"[{self.session_name}] session_config: {session_config}")
            if not await self.check_and_update_proxy(session_config):
                logger.error('Failed to find working proxy.')
                raise InvalidSession("No working proxy")

            tg_web_data, ref_data = await self.get_tg_web_data()
            if not await self.login(tg_web_data, ref_data):
                logger.error(f"[{self.session_name}] Login failed")
                raise InvalidSession("Login failed")

            if self._is_first_run:
                logger.info(f"{self.session_name} | Первый запуск, активируем онбординг")
                inventory = await self._get_inventory(limit=50, include="technical")
                onboarding_items = [item for item in inventory.get("inventory", []) if item.get("reward", {}).get("slug") == "main_onboarding"]
                if onboarding_items:
                    onboarding_item_id = onboarding_items[0].get("id")
                    await asyncio.sleep(uniform(2, 5))
                    if await self._activate_onboarding(onboarding_item_id):
                        logger.info(f"{self.session_name} | Онбординг активирован")
            
            while True:
                try:
                    await self.process_bot_logic()
                except InvalidSession as e:
                    logger.error(f"[{self.session_name}] InvalidSession: {e}")
                    if settings.DEBUG_LOGGING:
                        logger.debug(f"[{self.session_name}] InvalidSession details: {e}")
                    raise
                except Exception as error:
                    sleep_duration = uniform(60, 120)
                    logger.error(f"[{self.session_name}] Unknown error: {error}. Sleeping for {int(sleep_duration)}")
                    if settings.DEBUG_LOGGING:
                        logger.debug(f"[{self.session_name}] Exception details: {error}")
                    await asyncio.sleep(sleep_duration)

    async def process_bot_logic(self) -> None:
        """Основная логика бота для GiftFest"""
        try:
            emoji = self.EMOJI
            
            advent_quests = await self._get_quests(tag="gift_advent")
            
            if advent_quests:
                ready_advent = [
                    q for q in advent_quests if q.get("state") == "ready"
                ]
                
                if ready_advent:
                    logger.info(
                        f"{self.session_name} {emoji['success']} | "
                        f"Найдено {len(ready_advent)} карточек адвент-календаря"
                    )
                    
                    for quest in ready_advent:
                        quest_title = quest.get("title", "Unknown")
                        quest_id = quest.get("id")
                        quest_uuid = quest.get("uuid")
                        
                        logger.info(
                            f"{self.session_name} {emoji['miner']} | "
                            f"Открываем карточку '{quest_title}'"
                        )
                        
                        await asyncio.sleep(uniform(2, 5))
                        
                        collect_result = await self._collect_quest_reward(quest_uuid)
                        
                        if collect_result and collect_result.get("result"):
                            rewards = collect_result.get("rewards", [])
                            reward_amount = 1
                            for reward in rewards:
                                reward_slug = reward.get("slug", "")
                                reward_amount = reward.get("real_amount", 1)
                                
                                logger.info(
                                    f"{self.session_name} {emoji['success']} | "
                                    f"Получено: {reward_amount} {reward_slug}"
                                )
                            
                            if quest_id:
                                await asyncio.sleep(uniform(1, 2))
                                await self._send_advent_analytics(
                                    quest_id,
                                    reward_amount
                                )
                        else:
                            logger.warning(
                                f"{self.session_name} {emoji['warning']} | "
                                f"Не удалось открыть карточку"
                            )
            
            daily_quests = await self._get_quests(tag="gift_quests_daily")
            partner_quests = await self._get_quests(tag="gift_quests_partner")
            epic_quests = await self._get_quests(tag="gift_quests_epic")
            
            all_quests = (
                (daily_quests or []) +
                (partner_quests or []) +
                (epic_quests or [])
            )
            
            if all_quests:
                completed_quests = [
                    q for q in all_quests if q.get("state") == "completed"
                ]
                
                if completed_quests:
                    logger.info(f"{self.session_name} {emoji['success']} | Найдено {len(completed_quests)} выполненных квестов для сбора наград")
                    
                    for quest in completed_quests:
                        quest_title = quest.get("title", "Unknown")
                        quest_uuid = quest.get("uuid")
                        
                        logger.info(f"{self.session_name} {emoji['miner']} | Собираем награду за '{quest_title}'")
                        
                        await asyncio.sleep(uniform(2, 5))
                        
                        collect_result = await self._collect_quest_reward(quest_uuid)
                        
                        if collect_result and collect_result.get("result"):
                            rewards = collect_result.get("rewards", [])
                            for reward in rewards:
                                reward_type = reward.get("type", "unknown")
                                reward_slug = reward.get("slug", "")
                                reward_amount = reward.get("real_amount", 0)
                                
                                if reward_type == "lootbox":
                                    reward_title = reward.get("title", "Лутбокс")
                                    logger.info(f"{self.session_name} {emoji['success']} | Получен лутбокс: {reward_title}")
                                elif reward_slug:
                                    logger.info(f"{self.session_name} {emoji['success']} | Получено: {reward_amount} {reward_slug}")
                                else:
                                    logger.info(f"{self.session_name} {emoji['success']} | Получена награда")
                        else:
                            logger.warning(f"{self.session_name} {emoji['warning']} | Не удалось собрать награду")
            
            main_progress = await self._get_main_progress()
            
            if main_progress:
                completed_progress = [
                    q for q in main_progress if q.get("state") == "completed"
                ]
                
                if completed_progress:
                    logger.info(
                        f"{self.session_name} {emoji['success']} | "
                        f"Найдено {len(completed_progress)} наград основного прогресса"
                    )
                    
                    for quest in completed_progress:
                        quest_title = quest.get("title", "Unknown")
                        quest_id = quest.get("id")
                        quest_uuid = quest.get("uuid")
                        quest_type = quest.get("type", "unknown")
                        
                        logger.info(
                            f"{self.session_name} {emoji['miner']} | "
                            f"Собираем награду основного прогресса '{quest_title}'"
                        )
                        
                        await asyncio.sleep(uniform(2, 5))
                        
                        await self._send_main_progress_analytics(quest_id, quest_type)
                        
                        await asyncio.sleep(uniform(1, 2))
                        
                        collect_result = await self._collect_quest_reward(quest_uuid)
                        
                        if collect_result and collect_result.get("result"):
                            rewards = collect_result.get("rewards", [])
                            for reward in rewards:
                                reward_type = reward.get("type", "unknown")
                                reward_amount = reward.get("amount", 0)
                                reward_title = reward.get("title", "")
                                
                                if reward_type == "lottery_chances":
                                    logger.info(
                                        f"{self.session_name} {emoji['success']} | "
                                        f"Получено {reward_amount} билетов в розыгрыш"
                                    )
                                elif reward_type == "lootbox":
                                    logger.info(
                                        f"{self.session_name} {emoji['success']} | "
                                        f"Получен лутбокс: {reward_title}"
                                    )
                                elif reward_type == "game2048_item":
                                    logger.info(
                                        f"{self.session_name} {emoji['success']} | "
                                        f"Получен игровой предмет: {reward_title}"
                                    )
                                else:
                                    logger.info(
                                        f"{self.session_name} {emoji['success']} | "
                                        f"Получена награда: {reward_title or reward_type}"
                                    )
                        else:
                            logger.warning(
                                f"{self.session_name} {emoji['warning']} | "
                                f"Не удалось собрать награду"
                            )
            
            lootboxes = await self._get_lootboxes()
            
            if lootboxes:
                logger.info(f"{self.session_name} {emoji['info']} | Найдено {len(lootboxes)} типов лутбоксов")
                
                for lootbox_group in lootboxes:
                    reward_amount = lootbox_group.get("reward_amount", 0)
                    count = lootbox_group.get("count", 0)
                    title = lootbox_group.get("title", "Unknown")
                    
                    if count > 0:
                        logger.info(f"{self.session_name} {emoji['miner']} | Открываем {count}x '{title}'")
                        
                        await asyncio.sleep(uniform(2, 5))
                        
                        activate_result = await self._activate_lootboxes(reward_amount, "lootbox", count)
                        
                        if activate_result:
                            activated = activate_result.get("activated", 0)
                            rewards = activate_result.get("rewards", [])
                            
                            logger.info(f"{self.session_name} {emoji['success']} | Открыто {activated} лутбоксов")
                            
                            for reward in rewards:
                                reward_title = reward.get("title", "Unknown")
                                reward_type = reward.get("type", "unknown")
                                logger.info(f"{self.session_name} {emoji['success']} | Выпало: {reward_title} ({reward_type})")
                        else:
                            logger.warning(f"{self.session_name} {emoji['warning']} | Не удалось открыть лутбоксы")
            
            game_items = await self._get_game_items_inventory()
            
            if game_items:
                logger.info(f"{self.session_name} {emoji['info']} | В инвентаре {len(game_items)} игровых предметов")
                
                game_state = await self._get_game_state()
                cells = game_state.get("cells", [])
                empty_cells = [cell for cell in cells if not cell.get("item")]
                
                if empty_cells and game_items:
                    items_to_place = min(len(game_items), len(empty_cells))
                    
                    logger.info(f"{self.session_name} {emoji['miner']} | Размещаем {items_to_place} предметов на доске")
                    
                    for i in range(items_to_place):
                        item = game_items[i]
                        cell = empty_cells[i]
                        
                        item_id = item.get("id")
                        item_title = item.get("reward", {}).get("title", "Unknown")
                        cell_id = cell.get("id")
                        
                        await asyncio.sleep(uniform(1, 3))
                        
                        place_result = await self._place_item_on_board(cell_id, item_id)
                        
                        if place_result and place_result.get("field"):
                            logger.info(f"{self.session_name} {emoji['success']} | Размещен '{item_title}' на ячейке {cell_id}")
                        else:
                            logger.warning(f"{self.session_name} {emoji['warning']} | Не удалось разместить '{item_title}'")
            
            resources_data = await self._get_resources()
            resources = resources_data.get("resources", [])
            
            energy_resource = next((r for r in resources if r.get("slug") == "energy"), None)
            
            if energy_resource:
                energy_amount = energy_resource.get("amount", 0)
                energy_limit = energy_resource.get("limit", 0)
                last_spawned_at = energy_resource.get("last_spawned_at", 0)
                spawn_period = energy_resource.get("spawn_period_seconds", 600)
                
                logger.info(f"{self.session_name} {emoji['energy']} | Энергия: {energy_amount}/{energy_limit}")
                
                if energy_amount < 5:
                    current_time = int(time())
                    time_since_last_spawn = current_time - last_spawned_at
                    energy_to_restore = energy_limit - energy_amount
                    time_to_full = (energy_to_restore * spawn_period) - time_since_last_spawn
                    
                    if time_to_full > 0:
                        sleep_time = time_to_full + randint(10, 30)
                        logger.info(f"{self.session_name} {emoji['time']} | Недостаточно энергии. Сон на {sleep_time // 60} мин {sleep_time % 60} сек")
                        await asyncio.sleep(sleep_time)
                        return
            else:
                energy_amount = 0
            
            game_state = await self._get_game_state()
            
            if not game_state:
                logger.error(f"{self.session_name} | Не удалось получить состояние игры")
                await asyncio.sleep(60)
                return

            cells = game_state.get("cells", [])
            
            if not cells:
                logger.warning(f"{self.session_name} | Нет ячеек на поле")
                await asyncio.sleep(300)
                return
            
            filled_cells = [cell for cell in cells if cell.get("item")]
            empty_cells = [cell for cell in cells if not cell.get("item")]
            
            logger.info(f"{self.session_name} {emoji['info']} | Заполнено: {len(filled_cells)}/12, Пусто: {len(empty_cells)}/12")
            
            if len(empty_cells) > 0 and energy_amount >= 5:
                logger.info(f"{self.session_name} {emoji['miner']} | Спавним новый подарок (стоимость: 5 энергии)")
                
                await asyncio.sleep(uniform(1, 3))
                
                spawn_result = await self._spawn_gift()
                
                if spawn_result and spawn_result.get("field"):
                    logger.info(f"{self.session_name} {emoji['success']} | Подарок заспавнен")
                    energy_amount -= 5
                    
                    cells = spawn_result.get("field", {}).get("cells", [])
                    filled_cells = [cell for cell in cells if cell.get("item")]
                    empty_cells = [cell for cell in cells if not cell.get("item")]
                else:
                    logger.warning(f"{self.session_name} {emoji['warning']} | Не удалось заспавнить подарок")
            
            items_by_id = {}
            for cell in filled_cells:
                item = cell.get("item", {})
                item_id = item.get("id")
                if item_id:
                    if item_id not in items_by_id:
                        items_by_id[item_id] = []
                    items_by_id[item_id].append(cell)
            
            merged = False
            for item_id, cells_with_item in items_by_id.items():
                if len(cells_with_item) >= 2:
                    cell1 = cells_with_item[0]
                    cell2 = cells_with_item[1]
                    
                    item_title = cell1.get("item", {}).get("title", "Unknown")
                    
                    logger.info(f"{self.session_name} {emoji['miner']} | Объединяем '{item_title}' (ID: {item_id})")
                    
                    await asyncio.sleep(uniform(1, 2))
                    
                    result = await self._merge_cells(cell1.get("id"), cell2.get("id"))
                    
                    if result:
                        logger.info(f"{self.session_name} {emoji['success']} | Успешно объединено")
                        merged = True
                        break
                    else:
                        logger.warning(f"{self.session_name} {emoji['warning']} | Не удалось объединить")
            
            if not merged and len(empty_cells) == 0:
                logger.info(f"{self.session_name} {emoji['warning']} | Поле заполнено, нет ходов. Сжигаем самый дешевый предмет")
                
                cheapest_cell = min(filled_cells, key=lambda c: c.get("item", {}).get("id", 999))
                item_title = cheapest_cell.get("item", {}).get("title", "Unknown")
                
                logger.info(f"{self.session_name} {emoji['energy']} | Сжигаем '{item_title}'")
                
                await asyncio.sleep(uniform(1, 2))
                
                result = await self._burn_cell(cheapest_cell.get("id"))
                
                if result:
                    logger.info(f"{self.session_name} {emoji['success']} | Предмет сожжен")
                else:
                    logger.warning(f"{self.session_name} {emoji['error']} | Не удалось сжечь")
            
            await asyncio.sleep(uniform(1, 3))
            
        except Exception as e:
            logger.error(f"{self.session_name} | Ошибка в process_bot_logic: {str(e)}")
            if settings.DEBUG_LOGGING:
                import traceback
                logger.debug(f"[{self.session_name}] Traceback: {traceback.format_exc()}")
            await asyncio.sleep(60)

    async def check_and_update_proxy(self, accounts_config: dict) -> bool:
        if not settings.USE_PROXY:
            return True

        if not self._current_proxy or not await check_proxy(self._current_proxy):
            new_proxy = await get_working_proxy(accounts_config, self._current_proxy)
            if not new_proxy:
                return False

            self._current_proxy = new_proxy
            if self._http_client and not self._http_client.closed:
                await self._http_client.close()

            proxy_conn = {'connector': ProxyConnector.from_url(new_proxy)}
            self._http_client = CloudflareScraper(timeout=aiohttp.ClientTimeout(60), **proxy_conn)
            logger.info(f"{self.session_name} | Switched to new proxy: {new_proxy}")

        return True


class GiftFestBot(BaseBot):
    """Бот для работы с GiftFest"""
    
    _API_URL: str = "https://gift.stepcdn.space"

    def _get_auth_headers(self) -> dict:
        """Возвращает заголовки с авторизацией"""
        headers = get_giftfest_headers()
        if self._access_token:
            headers["authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _get_profile(self) -> dict:
        """Получает профиль пользователя"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _get_profile: headers={headers}")
            
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/profile",
            headers=headers
        )
        
        if not response:
            raise InvalidSession("Failed to get profile")
            
        return response

    async def _get_gifts(self) -> dict:
        """Получает список доступных подарков"""
        headers = self._get_auth_headers()
        
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/gifts",
            headers=headers
        )
        
        return response or {}

    async def _claim_gift(self, gift_id: str) -> bool:
        """Забирает подарок"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _claim_gift: gift_id={gift_id}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/gifts/{gift_id}/claim",
            headers=headers,
            json={}
        )
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _claim_gift response: {response}")
            
        return response is not None

    async def _get_game_state(self, field_id: int = 1) -> dict:
        """Получает состояние игрового поля"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _get_game_state: field_id={field_id}")
            
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/game2048/{field_id}/state",
            headers=headers
        )
        
        return response or {}

    async def _merge_cells(self, cell_id_1: int, cell_id_2: int) -> dict:
        """Объединяет две ячейки"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _merge_cells: {cell_id_1} + {cell_id_2}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/game2048/cells/merge",
            headers=headers,
            json={"cell_ids": [cell_id_1, cell_id_2]}
        )
        
        return response or {}

    async def _burn_cell(self, cell_id: int) -> dict:
        """Сжигает ячейку (удаляет предмет)"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _burn_cell: cell_id={cell_id}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/game2048/cells/{cell_id}/burn",
            headers=headers,
            json={}
        )
        
        return response or {}

    async def _get_resources(self) -> dict:
        """Получает информацию о ресурсах (энергия, опыт)"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _get_resources")
            
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/inventory/resources",
            headers=headers
        )
        
        return response or {}

    async def _spawn_gift(self, field_id: int = 1) -> dict:
        """Спавнит новый подарок на поле (стоит 5 энергии)"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _spawn_gift: field_id={field_id}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/game2048/{field_id}/spawn",
            headers=headers,
            json={}
        )
        
        return response or {}

    async def _get_quests(self, tag: str = "gift_quests_partner") -> List[dict]:
        """Получает список квестов"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _get_quests: tag={tag}")
            
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/wrapquests?tag={tag}",
            headers=headers
        )
        
        if isinstance(response, list):
            return response
        return []

    async def _collect_quest_reward(
        self,
        quest_uuid: Optional[str] = None
    ) -> dict:
        """Собирает награду за выполненный квест"""
        import uuid
        
        headers = self._get_auth_headers()
        headers["x-request-id"] = quest_uuid if quest_uuid else str(uuid.uuid4())
        headers["content-length"] = "0"
        
        if settings.DEBUG_LOGGING:
            logger.debug(
                f"[{self.session_name}] _collect_quest_reward: "
                f"quest_uuid={quest_uuid}"
            )
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/wrapquests/collect",
            headers=headers,
            json={}
        )
        
        return response or {}

    async def _send_advent_analytics(
        self,
        quest_id: int,
        reward_amount: int
    ) -> dict:
        """Отправляет аналитику после открытия карточки адвент-календаря"""
        from time import time
        
        headers = self._get_auth_headers()
        
        event_data = {
            "event_name": "advent_cancel_share_tap",
            "event_data": json.dumps({
                "reward_resource_amount": reward_amount,
                "quest_id": quest_id
            }),
            "page": "/advent",
            "client_timestamp": int(time()),
            "initiator": "ma_prod",
            "session": {
                "auth_date": int(time()),
                "language": "ru"
            },
            "device": {
                "user_agent": headers.get(
                    "user-agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                ),
                "browser": "edge",
                "browser_version": "142.0.0.0",
                "os": "windows"
            }
        }
        
        if settings.DEBUG_LOGGING:
            logger.debug(
                f"[{self.session_name}] _send_advent_analytics: "
                f"quest_id={quest_id}, reward_amount={reward_amount}"
            )
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/analytics/clientEvent",
            headers=headers,
            json=event_data
        )
        
        return response or {}

    async def _get_main_progress(self) -> list:
        """Получает список основного прогресса"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _get_main_progress")
            
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/wrapquests?tag=gift_main_progress&no_ord_done=true",
            headers=headers
        )
        
        if isinstance(response, list):
            return response
        return []

    async def _send_main_progress_analytics(
        self,
        quest_id: int,
        quest_type: str
    ) -> dict:
        """Отправляет аналитику перед сбором награды основного прогресса"""
        from time import time
        
        headers = self._get_auth_headers()
        
        event_data = {
            "event_name": "quest_collect_reward_tap",
            "event_data": json.dumps({
                "quest_id": quest_id,
                "quest_type": quest_type
            }),
            "page": "/giveaway",
            "client_timestamp": int(time()),
            "initiator": "ma_prod",
            "session": {
                "auth_date": int(time()),
                "language": "ru"
            },
            "device": {
                "user_agent": headers.get(
                    "user-agent",
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
                ),
                "browser": "edge",
                "browser_version": "142.0.0.0",
                "os": "windows"
            }
        }
        
        if settings.DEBUG_LOGGING:
            logger.debug(
                f"[{self.session_name}] _send_main_progress_analytics: "
                f"quest_id={quest_id}, quest_type={quest_type}"
            )
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/analytics/clientEvent",
            headers=headers,
            json=event_data
        )
        
        return response or {}

    async def _get_inventory(self, limit: int = 10, include: str = "promo_code") -> dict:
        """Получает инвентарь пользователя"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _get_inventory")
            
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/inventory?limit={limit}&include={include}&pagination=0",
            headers=headers
        )
        
        return response or {}

    async def _send_client_event(self, event_name: str, event_data: dict = None, page: str = "/quests") -> bool:
        """Отправляет клиентское событие (аналитика)"""
        headers = self._get_auth_headers()
        
        import uuid
        request_id = str(uuid.uuid4())
        headers["x-request-id"] = request_id
        
        client_timestamp = int(time())
        
        event_payload = {
            "event_name": event_name,
            "page": page,
            "client_timestamp": client_timestamp,
            "initiator": "ma_prod",
            "session": {
                "auth_date": client_timestamp,
                "language": "ru"
            },
            "device": {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36 Edg/142.0.0.0",
                "browser": "edge",
                "browser_version": "142.0.0.0",
                "os": "windows"
            }
        }
        
        if event_data:
            import json
            event_payload["event_data"] = json.dumps(event_data)
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _send_client_event: {event_name}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/analytics/clientEvent",
            headers=headers,
            json=event_payload
        )
        
        return response and response.get("result", False)

    async def _check_quest(self, quest_id: int) -> bool:
        """Проверяет выполнение партнерского квеста"""
        headers = self._get_auth_headers()
        
        import uuid
        request_id = str(uuid.uuid4())
        headers["x-request-id"] = request_id
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _check_quest: quest_id={quest_id}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/quests/{quest_id}",
            headers=headers,
            json={}
        )
        
        return response and response.get("result", False)

    async def _get_lootboxes(self) -> List[dict]:
        """Получает список лутбоксов в инвентаре"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _get_lootboxes")
            
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/inventory/group?type=lootbox",
            headers=headers
        )
        
        if response and isinstance(response.get("items"), list):
            return response.get("items", [])
        return []

    async def _activate_lootboxes(self, reward_amount: int, reward_type: str = "lootbox", limit: int = 1) -> dict:
        """Открывает лутбоксы"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _activate_lootboxes: amount={reward_amount}, type={reward_type}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/inventory/activate/all",
            headers=headers,
            json={
                "reward_amount": reward_amount,
                "reward_type": reward_type,
                "limit": limit
            }
        )
        
        return response or {}

    async def _place_item_on_board(self, cell_id: int, inventory_item_id: int) -> dict:
        """Размещает предмет из инвентаря на игровую доску"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _place_item_on_board: cell_id={cell_id}, inventory_item_id={inventory_item_id}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/game2048/cells/{cell_id}/place",
            headers=headers,
            json={"inventory_item_id": inventory_item_id}
        )
        
        return response or {}

    async def _get_game_items_inventory(self) -> List[dict]:
        """Получает список игровых предметов в инвентаре"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _get_game_items_inventory")
            
        response = await self.make_request(
            method="GET",
            url=f"{self._API_URL}/inventory?limit=50&include=game2048_item&pagination=0",
            headers=headers
        )
        
        if response and isinstance(response.get("inventory"), list):
            return response.get("inventory", [])
        return []

    async def _activate_onboarding(self, item_id: int) -> bool:
        """Активирует онбординг при первом запуске"""
        headers = self._get_auth_headers()
        
        if settings.DEBUG_LOGGING:
            logger.debug(f"[{self.session_name}] _activate_onboarding: item_id={item_id}")
            
        response = await self.make_request(
            method="POST",
            url=f"{self._API_URL}/inventory/activate",
            headers=headers,
            json={"item_id": item_id}
        )
        
        return response and response.get("result", False)


async def run_tapper(tg_client: UniversalTelegramClient):
    bot = GiftFestBot(tg_client=tg_client)
    try:
        await bot.run()
    except InvalidSession as e:
        logger.error(f"Invalid Session: {e}")
        raise
