"""Plan-Execute grafiğinin akış boyunca taşıdığı durum (state)."""

from __future__ import annotations

import operator
from typing import Annotated, List, Tuple

from typing_extensions import TypedDict


class PlanExecute(TypedDict):
    """Graf boyunca güncellenen ortak durum.

    - input:       Kullanıcının orijinal görevi.
    - plan:        Henüz yürütülmemiş adımlar.
    - past_steps:  (adım, sonuç) çiftleri; operator.add ile birikerek eklenir.
    - response:    Doluysa graf sonlanır ve bu cevap döner.
    """

    input: str
    plan: List[str]
    past_steps: Annotated[List[Tuple[str, str]], operator.add]
    response: str
