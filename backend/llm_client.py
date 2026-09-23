from abc import ABC, abstractmethod
from typing import Dict, List


class LLMClient(ABC):
    @abstractmethod
    async def complete(self, messages: List[Dict], system: str = "") -> str:
        pass
