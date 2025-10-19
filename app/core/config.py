from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Optional

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding='utf-8',
        extra="ignore"
    )

    APP_NAME: str = "machinetranslation-2api"
    APP_VERSION: str = "1.0.0"
    DESCRIPTION: str = "一个将 machinetranslation.com 转换为兼容 OpenAI 格式 API 的高性能代理。"

    API_MASTER_KEY: Optional[str] = "sk-machinetranslation-2api-default-key"
    NGINX_PORT: int = 8088

    # 上游 API 配置
    # 从抓包中提取的静态 API Key
    MT_API_KEY: str = "pDwCjq7CyeAmn1Z3osNunACg2U0SLIhwBTtsp1WqYFMf5UuSIvMBYGS4pt8OIsGMH"
    BASE_API_URL: str = "https://api.machinetranslation.com/v1"
    SOCKET_URL: str = "https://ss.machinetranslation.com"
    
    API_REQUEST_TIMEOUT: int = 120
    SOCKET_TIMEOUT: int = 60 # 等待 socket.io 结果的超时时间

    # 模型列表
    KNOWN_MODELS: List[str] = [
        "machinetranslation-best", "chat_gpt", "gemini", "claude", "libre", "mistral_ai", "smart"
    ]
    DEFAULT_MODEL: str = "machinetranslation-best"
    # 用于获取最终评分报告的模型
    SCORER_MODEL: str = "gpt-4o-mini"
    # 翻译请求中要包含的模型列表
    LLM_LIST_FOR_REQUEST: List[str] = ["chat_gpt", "gemini", "claude", "libre", "mistral_ai"]


settings = Settings()
