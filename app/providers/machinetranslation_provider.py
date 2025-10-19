import asyncio
import json
import time
import uuid
import re
from typing import Dict, Any, List, AsyncGenerator

import httpx
from fastapi import HTTPException
from loguru import logger

from app.core.config import settings
from app.utils.sse_utils import create_sse_data, create_chat_completion_chunk, DONE_CHUNK

# --- 终极日志模块 ---
async def log_request(request):
    logger.debug(f">>> REQUEST: {request.method} {request.url}")
    logger.trace(f"    HEADERS: {request.headers}")
    try:
        content = request.content.decode('utf-8')
        if content: logger.trace(f"    BODY: {content}")
    except (UnicodeDecodeError, AttributeError):
        pass

async def log_response(response):
    await response.aread()
    logger.debug(f"<<< RESPONSE: {response.status_code} {response.request.method} {response.request.url}")
    logger.trace(f"    HEADERS: {response.headers}")
    try:
        content = response.text
        if content: logger.trace(f"    BODY: {content}")
    except Exception:
        pass
# --- 终极日志模块结束 ---


class MachineTranslationProvider:
    def __init__(self):
        self.client = httpx.AsyncClient(
            timeout=settings.API_REQUEST_TIMEOUT,
            event_hooks={'request': [log_request], 'response': [log_response]}
        )
        self.base_headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.machinetranslation.com",
            "Referer": "https://www.machinetranslation.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
        }
        self.api_headers = {**self.base_headers, "api-key": settings.MT_API_KEY}

    async def _get_share_id(self, text: str, source_lang: str, target_lang: str) -> str:
        url = f"{settings.BASE_API_URL}/translation/share-id"
        payload = {
            "text": text, "source_language_code": source_lang, "target_language_code": target_lang,
            "s3_file_path": None, "total_words": None, "secure_mode": False,
            "total_words_in_file": None, "is_doc_translation_disabled": False, "doc_translation_disabled_reason": ""
        }
        response = await self.client.post(url, headers=self.api_headers, json=payload)
        response.raise_for_status()
        data = response.json()
        share_id = data.get("share_id")
        if not share_id: raise ValueError("API 响应中缺少 'share_id'")
        logger.info(f"成功获取 share_id: {share_id}")
        return share_id

    async def _manual_socket_io_flow(self, share_id: str) -> List[Dict]:
        sid = None
        try:
            t_param = f"{int(time.time() * 1000)}"
            handshake_url = f"{settings.SOCKET_URL}/socket.io/?EIO=4&transport=polling&t={t_param}"
            logger.info(f"[Socket-IO] 步骤 1: 发送握手请求")
            response = await self.client.get(handshake_url, headers=self.base_headers)
            response.raise_for_status()
            
            raw_data = response.text
            match = re.search(r'"sid":"([^"]+)"', raw_data)
            if not match: raise ValueError("无法从握手响应中解析 sid")
            sid = match.group(1)
            logger.success(f"[Socket-IO] 成功获取 sid: {sid}")

            post_url = f"{settings.SOCKET_URL}/socket.io/?EIO=4&transport=polling&sid={sid}"
            
            connect_payload = f'40{json.dumps({"shareId": share_id}, separators=(",", ":"))}'
            logger.info("[Socket-IO] 步骤 2a: 发送带 shareId 的 CONNECT (40) 包")
            await self.client.post(post_url, headers={**self.base_headers, "Content-Type": "text/plain;charset=UTF-8"}, data=connect_payload.encode('utf-8'))
            
            event_payload = f'42["llm:translation:request",{{"shareId":"{share_id}","llmList":{json.dumps(settings.LLM_LIST_FOR_REQUEST)}}}]'
            logger.info(f"[Socket-IO] 步骤 2b: 发送 EVENT 包")
            await self.client.post(post_url, headers={**self.base_headers, "Content-Type": "text/plain;charset=UTF-8"}, data=event_payload.encode('utf-8'))

            logger.info("[Socket-IO] 步骤 3: 开始长轮询获取结果...")
            results = []
            start_time = time.time()
            expected_results = len(settings.LLM_LIST_FOR_REQUEST)

            while time.time() - start_time < settings.SOCKET_TIMEOUT and len(results) < expected_results:
                t_param = f"{int(time.time() * 1000)}"
                poll_url = f"{settings.SOCKET_URL}/socket.io/?EIO=4&transport=polling&t={t_param}&sid={sid}"
                
                try:
                    poll_response = await self.client.get(poll_url, headers=self.base_headers, timeout=30)
                    poll_response.raise_for_status()
                    raw_poll_data = poll_response.text
                    
                    packets = raw_poll_data.split('')
                    for packet in packets:
                        if not packet: continue
                        if packet == '2':
                            logger.info("[Socket-IO] 收到 PING(2)，立即回复 PONG(3)")
                            await self.client.post(post_url, headers=self.base_headers, content='3')
                            continue
                        if packet.startswith('44'):
                            raise Exception(f"上游拒绝会话: {packet}")
                        if packet.startswith('42'):
                            logger.success(f"[Socket-IO] 收到数据包: {packet}")
                            try:
                                data = json.loads(packet[2:])
                                if data[0] == "llm:translation:response":
                                    results.append(data[1])
                                    logger.success(f"成功解析翻译结果 ({data[1].get('llm')})。进度: {len(results)}/{expected_results}")
                            except (json.JSONDecodeError, IndexError):
                                logger.warning(f"无法解析事件包: {packet}")
                except httpx.ReadTimeout:
                    logger.info("[Socket-IO] 轮询超时，继续...")
                    continue
                except Exception as e:
                    logger.error(f"[Socket-IO] 轮询期间发生错误: {e}")
                    break
            return results
        except Exception as e:
            logger.error(f"手动 Socket.IO 流程失败: {e}", exc_info=True)
            return []

    async def _get_final_scores(self, share_id: str) -> Dict:
        url = f"{settings.BASE_API_URL}/translation/score_test/{share_id}/{settings.SCORER_MODEL}"
        await asyncio.sleep(5)
        response = await self.client.post(url, headers=self.api_headers, content=b'', timeout=60)
        response.raise_for_status()
        data = response.json()
        logger.success(f"成功获取 share_id '{share_id}' 的最终评分报告。")
        return data

    def _format_markdown_content(self, model: str, final_data: Dict) -> str:
        translations = final_data.get("translations", [])
        if not translations:
            return "错误：上游未返回任何翻译结果。"

        best_translation = max(translations, key=lambda t: t.get("score") or 0)
        
        if model == "machinetranslation-best":
            main_content = best_translation.get("target_text", "No translation found.")
        else:
            specific_model_translation = next((t for t in translations if t.get("engine") == model), best_translation)
            main_content = specific_model_translation.get("target_text", "No translation found.")

        final_content = main_content.strip()
        final_content += "\n\n---\n\n### 详细翻译报告\n"
        
        sorted_translations = sorted(translations, key=lambda t: t.get("score") or 0, reverse=True)
        
        for t in sorted_translations:
            engine = t.get("engine", "N/A")
            score = t.get("score")
            score_str = f"{score:.2f}" if score is not None else "N/A"
            text = t.get("target_text", "").strip()
            
            final_content += f"\n**模型: {engine}** (评分: {score_str})\n"
            final_content += f"> {text}\n"
        
        return final_content

    async def translate_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[bytes, None]:
        request_id = f"chatcmpl-{uuid.uuid4()}"
        model = request_data.get("model", settings.DEFAULT_MODEL)
        
        try:
            messages = request_data.get("messages", [])
            text_to_translate = next((m['content'] for m in reversed(messages) if m.get('role') == 'user'), None)
            if not text_to_translate:
                raise HTTPException(status_code=400, detail="未找到用户输入内容。")

            share_id = await self._get_share_id(text_to_translate, "auto", "en")
            
            socket_results = await self._manual_socket_io_flow(share_id)
            if not socket_results:
                logger.warning("Socket 流程未返回结果，但仍尝试获取最终评分报告...")
            
            final_data = await self._get_final_scores(share_id)
            
            if not final_data.get("translations"):
                 raise HTTPException(status_code=502, detail="上游未返回任何翻译结果。")

            markdown_content = self._format_markdown_content(model, final_data)

            # 以流的形式一次性发送所有内容
            chunk = create_chat_completion_chunk(request_id, model, markdown_content)
            yield create_sse_data(chunk)

            # 发送结束标志
            final_chunk = create_chat_completion_chunk(request_id, model, "", finish_reason="stop")
            yield create_sse_data(final_chunk)

        except Exception as e:
            logger.error(f"流式处理中发生错误: {e}", exc_info=True)
            error_message = f"处理请求时发生错误: {str(e)}"
            chunk = create_chat_completion_chunk(request_id, model, error_message, finish_reason="error")
            yield create_sse_data(chunk)
        
        finally:
            yield DONE_CHUNK

    async def get_models(self) -> Dict:
        return {
            "object": "list",
            "data": [
                {"id": name, "object": "model", "created": int(time.time()), "owned_by": "lzA6"}
                for name in settings.KNOWN_MODELS
            ]
        }
