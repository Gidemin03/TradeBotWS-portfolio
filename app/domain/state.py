import logging
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.domain.models import BotStatus, LiveSearchPreset, Event, MerchantGrid, InventoryGrid, InventoryPatches

logger = logging.getLogger(__name__)


class BotContext(BaseModel):
    """
    Р"Р>Р?Р+Р°Р>С?Р?Р?Рч С?Р?С?С'Р?С?Р?РёРч Р+Р?С'Р°.
    """
    bot_status: BotStatus = BotStatus.IDLE
    # id РїС?РчС?РчС'Р° -> РїС?РчС?РчС'
    live_search_presets: Dict[str, LiveSearchPreset] = Field(default_factory=dict)
    # РїР?С?Р>РчР?Р?РёРч С?Р?Р+С<С'РёС?
    events: List[Event] = Field(default_factory=list)
    merchant_grid: MerchantGrid | None = None
    inventory_grid: InventoryGrid | None = None
    inventory_patches: InventoryPatches | None = None
    poe_sessid: str | None = None
    cf_clearance: str | None = None
    sse_manager: Any | None = None

    class Config:
        arbitrary_types_allowed = True

    # --- Р?РчС'Р?Р?С<-С?С'РёР>РёС'С< ---

    def add_preset(self, preset: LiveSearchPreset) -> None:
        self.live_search_presets[preset.id] = preset

    def list_presets(self) -> list[LiveSearchPreset]:
        return list(self.live_search_presets.values())

    def remove_preset(self, preset_id: str) -> None:
        self.live_search_presets.pop(preset_id, None)

    def push_event(self, event: Event, max_events: int = 200) -> None:
        self.events.append(event)
        if len(self.events) > max_events:
            # Р?С?С'Р°Р?Р>С?РчР? С'Р?Р>С?РєР? РїР?С?Р>РчР?Р?РёРч max_events
            self.events = self.events[-max_events:]
        if self.sse_manager:
            try:
                self.sse_manager.publish_event(event)
                self.sse_manager.publish_status(
                    bot_status=self.bot_status,
                    presets_count=len(self.live_search_presets),
                    events_count=len(self.events),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("SSE publish failed: %r", exc)

    def set_auth(self, poe_sessid: str | None, cf_clearance: str | None) -> None:
        self.poe_sessid = poe_sessid
        self.cf_clearance = cf_clearance
