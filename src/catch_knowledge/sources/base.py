from __future__ import annotations

from abc import ABC, abstractmethod
from typing import List

from catch_knowledge.domain import CollectedPost


class BaseCollector(ABC):
    @abstractmethod
    def collect(self) -> List[CollectedPost]:
        raise NotImplementedError
