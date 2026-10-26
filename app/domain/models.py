# domain/models.py

from enum import Enum
from typing import Optional
import uuid

from pydantic import BaseModel, HttpUrl


class BotStatus(str, Enum):
    IDLE = "idle"
    IN_TELEPORTING = "in_teleporting"
    BUSY = "busy"


class MerchantGrid(BaseModel):
    """
    Нормализованная сетка торговца (12x12) в долях экрана.
    """

    screen_width: int
    screen_height: int
    left: float
    top: float
    right: float
    bottom: float
    rows: int = 12
    cols: int = 12

    class Config:
        validate_assignment = True

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def to_pixels(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> tuple[float, float, float, float]:
        sw = float(width or self.screen_width)
        sh = float(height or self.screen_height)
        return (
            self.left * sw,
            self.top * sh,
            self.right * sw,
            self.bottom * sh,
        )

    def slot_to_screen(
        self,
        x: int,
        y: int,
        width: int | None = None,
        height: int | None = None,
    ) -> tuple[int, int]:
        sw = float(width or self.screen_width)
        sh = float(height or self.screen_height)
        if x < 0 or y < 0 or x >= self.cols or y >= self.rows:
            raise ValueError("Slot out of range")

        cell_w = (self.right - self.left) * sw / self.cols
        cell_h = (self.bottom - self.top) * sh / self.rows
        px = (self.left * sw) + (x + 0.5) * cell_w
        py = (self.top * sh) + (y + 0.5) * cell_h
        return int(round(px)), int(round(py))

    @classmethod
    def from_rect(
        cls,
        rect: tuple[float, float, float, float],
        screen_width: int,
        screen_height: int,
        rows: int = 12,
        cols: int = 12,
    ) -> "MerchantGrid":
        left, top, right, bottom = rect
        if right <= left or bottom <= top:
            raise ValueError("Invalid rectangle size")
        sw = float(screen_width)
        sh = float(screen_height)
        return cls(
            screen_width=screen_width,
            screen_height=screen_height,
            left=left / sw,
            top=top / sh,
            right=right / sw,
            bottom=bottom / sh,
            rows=rows,
            cols=cols,
        )


class InventoryGrid(BaseModel):
    """
    Нормализованная сетка инвентаря (12x5) в долях экрана.
    """

    screen_width: int
    screen_height: int
    left: float
    top: float
    right: float
    bottom: float
    rows: int = 5
    cols: int = 12

    class Config:
        validate_assignment = True

    @property
    def width(self) -> float:
        return self.right - self.left

    @property
    def height(self) -> float:
        return self.bottom - self.top

    def to_pixels(
        self,
        width: int | None = None,
        height: int | None = None,
    ) -> tuple[float, float, float, float]:
        sw = float(width or self.screen_width)
        sh = float(height or self.screen_height)
        return (
            self.left * sw,
            self.top * sh,
            self.right * sw,
            self.bottom * sh,
        )

    def slot_to_screen(
        self,
        x: int,
        y: int,
        width: int | None = None,
        height: int | None = None,
    ) -> tuple[int, int]:
        sw = float(width or self.screen_width)
        sh = float(height or self.screen_height)
        if x < 0 or y < 0 or x >= self.cols or y >= self.rows:
            raise ValueError("Slot out of range")

        cell_w = (self.right - self.left) * sw / self.cols
        cell_h = (self.bottom - self.top) * sh / self.rows
        px = (self.left * sw) + (x + 0.5) * cell_w
        py = (self.top * sh) + (y + 0.5) * cell_h
        return int(round(px)), int(round(py))

    @classmethod
    def from_rect(
        cls,
        rect: tuple[float, float, float, float],
        screen_width: int,
        screen_height: int,
        rows: int = 5,
        cols: int = 12,
    ) -> "InventoryGrid":
        left, top, right, bottom = rect
        if right <= left or bottom <= top:
            raise ValueError("Invalid rectangle size")
        sw = float(screen_width)
        sh = float(screen_height)
        return cls(
            screen_width=screen_width,
            screen_height=screen_height,
            left=left / sw,
            top=top / sh,
            right=right / sw,
            bottom=bottom / sh,
            rows=rows,
            cols=cols,
        )


class InventoryPatch(BaseModel):
    """
    Base64-снимок патча (RGBA/BGRA) фиксированного размера.
    """

    data_b64: str
    width: int
    height: int
    channels: int = 4


class InventoryPatches(BaseModel):
    """
    Пара патчей над верхними ячейками инвентаря.
    """

    left: InventoryPatch
    right: InventoryPatch
    screen_width: int
    screen_height: int


class FetchedItem(BaseModel):
    """
    Описание найденного предмета для обработки ботом.
    """

    preset_id: str
    item_id: str
    hideout_token: str
    stash_x: int
    stash_y: int


class LiveSearchStatus(str, Enum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    SEARCHING = "searching"
    ERROR = "error"


class LiveSearchMode(str, Enum):
    ONLY_TP = "only_tp"
    AUTOBUY = "autobuy"


class LiveSearchPreset(BaseModel):
    id: str
    name: str
    url: HttpUrl
    mode: LiveSearchMode = LiveSearchMode.ONLY_TP
    status: LiveSearchStatus = LiveSearchStatus.STOPPED

    @classmethod
    def from_url(
        cls,
        url: HttpUrl,
        name: Optional[str] = None,
        mode: Optional[LiveSearchMode] = None,
    ) -> "LiveSearchPreset":
        return cls(
            id=str(uuid.uuid4()),
            name=name or "Live search preset",
            url=url,
            mode=mode or LiveSearchMode.ONLY_TP,
        )


class EventLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class Event(BaseModel):
    level: EventLevel
    message: str
