from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Optional
import base64

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen, QImage
from PySide6.QtWidgets import QApplication, QWidget

from app.domain.models import MerchantGrid, InventoryGrid, InventoryPatch, InventoryPatches
from app.domain.state import BotContext
from app.storage import load_state, save_state


def _grab_patch(screen, x: float, y: float, width: float, height: float) -> InventoryPatch:
    px = int(round(x))
    py = int(round(y))
    pw = max(1, int(round(width)))
    ph = max(1, int(round(height)))
    geo = screen.geometry()
    if px < geo.left():
        pw -= geo.left() - px
        px = geo.left()
    if py < geo.top():
        ph -= geo.top() - py
        py = geo.top()
    pw = min(pw, geo.width() - (px - geo.left()))
    ph = min(ph, geo.height() - (py - geo.top()))
    if pw <= 0 or ph <= 0:
        raise ValueError("Patch rectangle is outside of the screen")
    pixmap = screen.grabWindow(0, px, py, pw, ph)
    image = pixmap.toImage().convertToFormat(QImage.Format_RGBA8888)
    byte_count = image.sizeInBytes() if hasattr(image, "sizeInBytes") else image.bytesPerLine() * image.height()
    buf_view = memoryview(image.constBits())[:byte_count]
    data = bytes(buf_view)
    return InventoryPatch(
        data_b64=base64.b64encode(data).decode("ascii"),
        width=pw,
        height=ph,
        channels=4,
    )


def capture_inventory_patches(screen, grid: InventoryGrid) -> InventoryPatches:
    left, top, right, bottom = grid.to_pixels()
    cell_w = (right - left) / grid.cols
    cell_h = (bottom - top) / grid.rows
    y_top = max(0.0, top - cell_h)  # одна клетка вверх
    left_patch = _grab_patch(screen, x=left, y=y_top, width=cell_w, height=cell_h)
    right_patch = _grab_patch(
        screen,
        x=left + (grid.cols - 1) * cell_w,
        y=y_top,
        width=cell_w,
        height=cell_h,
    )
    return InventoryPatches(
        left=left_patch,
        right=right_patch,
        screen_width=grid.screen_width,
        screen_height=grid.screen_height,
    )


class OverlayWindow(QWidget):
    """
    Прозрачное окно для калибровки: рисуем прямоугольник сетки, поддерживаем resize по краям/углам.
    """

    def __init__(
        self,
        screen_rect: QRectF,
        screen,
        initial_rect: Optional[QRectF],
        initial_inventory_rect: Optional[QRectF],
        on_save: Callable[[MerchantGrid, InventoryGrid, Optional[InventoryPatches]], None],
    ) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowStaysOnTopHint
            | Qt.FramelessWindowHint
            | Qt.Window
        )
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.showFullScreen()

        self._screen = screen
        self._screen_rect = screen_rect
        self._merchant_rect: Optional[QRectF] = initial_rect
        self._inventory_rect: Optional[QRectF] = initial_inventory_rect
        self._resize_mode: Optional[str] = None
        self._drag_origin: Optional[QPointF] = None
        self._origin_rect: Optional[QRectF] = None
        self._active: Optional[str] = None  # "merchant" | "inventory"
        self._on_save = on_save
        self._margin = 10

        # если сеток нет, показать стартовые прямоугольники
        self._init_defaults()

    # --- events ---

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())
        self._set_active_by_pos(pos)

        rect = self._get_active_rect()
        if rect is None:
            return

        mode = self._hit_test(rect, pos)
        self._drag_origin = pos
        self._origin_rect = QRectF(rect)

        if mode:
            self._resize_mode = mode
            return

        if rect.contains(pos):
            self._resize_mode = "move"
            return

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        pos = event.position() if hasattr(event, "position") else QPointF(event.pos())

        if self._resize_mode and self._origin_rect and self._drag_origin:
            rect = QRectF(self._origin_rect)
            dx = pos.x() - self._drag_origin.x()
            dy = pos.y() - self._drag_origin.y()

            if self._resize_mode == "move":
                rect.translate(dx, dy)
            else:
                if "left" in self._resize_mode:
                    rect.setLeft(rect.left() + dx)
                if "right" in self._resize_mode:
                    rect.setRight(rect.right() + dx)
                if "top" in self._resize_mode:
                    rect.setTop(rect.top() + dy)
                if "bottom" in self._resize_mode:
                    rect.setBottom(rect.bottom() + dy)

            self._set_active_rect(rect.normalized())
            self.update()
            return

        # hover feedback + выбор активной сетки
        self._set_active_by_pos(pos)
        rect = self._get_active_rect()
        if rect:
            mode = self._hit_test(rect, pos)
            self._set_cursor(mode)
        else:
            self._set_cursor(None)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return
        self._resize_mode = None
        self._origin_rect = None
        self._drag_origin = None
        self.update()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            self.close()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._handle_save()

    # --- helpers ---

    def _hit_test(self, pos: QPointF) -> Optional[str]:
        if not self._grid_rect:
            return None
        r = self._grid_rect
        margin = self._margin
        left = abs(pos.x() - r.left()) <= margin
        right = abs(pos.x() - r.right()) <= margin
        top = abs(pos.y() - r.top()) <= margin
        bottom = abs(pos.y() - r.bottom()) <= margin

        if top and left:
            return "top_left"
        if top and right:
            return "top_right"
        if bottom and left:
            return "bottom_left"
        if bottom and right:
            return "bottom_right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return None

    def _set_cursor(self, mode: Optional[str]) -> None:
        mapping = {
            "left": Qt.SizeHorCursor,
            "right": Qt.SizeHorCursor,
            "top": Qt.SizeVerCursor,
            "bottom": Qt.SizeVerCursor,
            "top_left": Qt.SizeFDiagCursor,
            "bottom_right": Qt.SizeFDiagCursor,
            "top_right": Qt.SizeBDiagCursor,
            "bottom_left": Qt.SizeBDiagCursor,
        }
        if not mode:
            self.unsetCursor()
            return
        self.setCursor(mapping.get(mode, Qt.ArrowCursor))

    def _handle_save(self) -> None:
        if not self._merchant_rect or not self._inventory_rect:
            return
        if self._merchant_rect.width() < 20 or self._merchant_rect.height() < 20:
            return
        if self._inventory_rect.width() < 20 or self._inventory_rect.height() < 20:
            return
        grid = MerchantGrid.from_rect(
            rect=(
                self._merchant_rect.left(),
                self._merchant_rect.top(),
                self._merchant_rect.right(),
                self._merchant_rect.bottom(),
            ),
            screen_width=int(self._screen_rect.width()),
            screen_height=int(self._screen_rect.height()),
        )
        inv_grid = InventoryGrid.from_rect(
            rect=(
                self._inventory_rect.left(),
                self._inventory_rect.top(),
                self._inventory_rect.right(),
                self._inventory_rect.bottom(),
            ),
            screen_width=int(self._screen_rect.width()),
            screen_height=int(self._screen_rect.height()),
        )
        # Прячем затемнённый оверлей перед съёмом патчей, чтобы не влиял альфа-слой
        self.hide()
        QApplication.processEvents()
        patches = None
        try:
            patches = capture_inventory_patches(self._screen, inv_grid)
            print(
                "Inventory patches captured: "
                f"L {patches.left.width}x{patches.left.height}, "
                f"R {patches.right.width}x{patches.right.height}"
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Failed to capture inventory patches: {exc}")
        self._on_save(grid, inv_grid, patches)
        self.close()

    # --- paint ---

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # dim background
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))
        painter.setPen(QColor("#ffffff"))
        painter.drawText(40, 40, "Открой merchant + inventory и нажми Enter для сохранения")

        # merchant grid
        if self._merchant_rect:
            self._draw_grid(
                painter,
                rect=self._merchant_rect,
                rows=12,
                cols=12,
                color=QColor("#A855F7"),
                active=self._active == "merchant",
            )

        # inventory grid
        if self._inventory_rect:
            self._draw_grid(
                painter,
                rect=self._inventory_rect,
                rows=5,
                cols=12,
                color=QColor("#ef4444"),  # red
                active=self._active == "inventory",
            )

    # --- helpers: geometry ---

    def _init_defaults(self) -> None:
        if self._merchant_rect is None:
            w = self._screen_rect.width() * 0.4
            h = self._screen_rect.height() * 0.4
            cx = self._screen_rect.width() * 0.5
            cy = self._screen_rect.height() * 0.45
            self._merchant_rect = QRectF(
                cx - w / 2,
                cy - h / 2,
                w,
                h,
            )
        if self._inventory_rect is None:
            # по умолчанию справа-снизу от merchant
            base = self._merchant_rect
            margin_x = self._screen_rect.width() * 0.05
            margin_y = self._screen_rect.height() * 0.05
            w = base.width()
            h = self._screen_rect.height() * 0.2
            left = min(base.right() + margin_x, self._screen_rect.width() - w - margin_x)
            top = min(base.bottom() + margin_y, self._screen_rect.height() - h - margin_y)
            self._inventory_rect = QRectF(left, top, w, h)

    def _get_active_rect(self) -> Optional[QRectF]:
        if self._active == "merchant":
            return self._merchant_rect
        if self._active == "inventory":
            return self._inventory_rect
        return None

    def _set_active_rect(self, rect: QRectF) -> None:
        if self._active == "merchant":
            self._merchant_rect = rect
        elif self._active == "inventory":
            self._inventory_rect = rect

    def _set_active_by_pos(self, pos: QPointF) -> None:
        # inventory приоритетнее, если пересечение
        if self._inventory_rect and self._inventory_rect.adjusted(-self._margin, -self._margin, self._margin, self._margin).contains(pos):
            self._active = "inventory"
            return
        if self._merchant_rect and self._merchant_rect.adjusted(-self._margin, -self._margin, self._margin, self._margin).contains(pos):
            self._active = "merchant"
            return
        self._active = None

    def _hit_test(self, rect: QRectF, pos: QPointF) -> Optional[str]:
        margin = self._margin
        left = abs(pos.x() - rect.left()) <= margin
        right = abs(pos.x() - rect.right()) <= margin
        top = abs(pos.y() - rect.top()) <= margin
        bottom = abs(pos.y() - rect.bottom()) <= margin

        if top and left:
            return "top_left"
        if top and right:
            return "top_right"
        if bottom and left:
            return "bottom_left"
        if bottom and right:
            return "bottom_right"
        if left:
            return "left"
        if right:
            return "right"
        if top:
            return "top"
        if bottom:
            return "bottom"
        return None

    def _set_cursor(self, mode: Optional[str]) -> None:
        mapping = {
            "left": Qt.SizeHorCursor,
            "right": Qt.SizeHorCursor,
            "top": Qt.SizeVerCursor,
            "bottom": Qt.SizeVerCursor,
            "top_left": Qt.SizeFDiagCursor,
            "bottom_right": Qt.SizeFDiagCursor,
            "top_right": Qt.SizeBDiagCursor,
            "bottom_left": Qt.SizeBDiagCursor,
        }
        if not mode:
            self.unsetCursor()
            return
        self.setCursor(mapping.get(mode, Qt.ArrowCursor))

    def _draw_grid(
        self,
        painter: QPainter,
        rect: QRectF,
        rows: int,
        cols: int,
        color: QColor,
        active: bool = False,
    ) -> None:
        pen = QPen(color)
        pen.setWidth(3 if active else 2)
        painter.setPen(pen)
        painter.drawRect(rect)

        cell_w = rect.width() / cols
        cell_h = rect.height() / rows
        inner_pen = QPen(QColor(color.red(), color.green(), color.blue(), 140))
        inner_pen.setWidth(1)
        painter.setPen(inner_pen)
        for i in range(1, cols):
            x = rect.left() + cell_w * i
            painter.drawLine(int(x), int(rect.top()), int(x), int(rect.bottom()))
        for j in range(1, rows):
            y = rect.top() + cell_h * j
            painter.drawLine(int(rect.left()), int(y), int(rect.right()), int(y))

        painter.fillRect(rect, QColor(color.red(), color.green(), color.blue(), 40))


def run_calibrator(state_path: Path) -> None:
    ctx = BotContext()
    load_state(state_path, ctx)

    app = QApplication(sys.argv)
    screen = QGuiApplication.primaryScreen()
    if not screen:
        raise RuntimeError("Не удалось определить экран")
    geometry = screen.geometry()
    screen_rect = QRectF(
        float(geometry.left()),
        float(geometry.top()),
        float(geometry.width()),
        float(geometry.height()),
    )

    merchant_rect = None
    inventory_rect = None
    if ctx.merchant_grid:
        left, top, right, bottom = ctx.merchant_grid.to_pixels()
        merchant_rect = QRectF(left, top, right - left, bottom - top)
    if ctx.inventory_grid:
        left, top, right, bottom = ctx.inventory_grid.to_pixels()
        inventory_rect = QRectF(left, top, right - left, bottom - top)

    def _save(grid: MerchantGrid, inventory_grid: InventoryGrid, patches: Optional[InventoryPatches]) -> None:
        ctx.merchant_grid = grid
        ctx.inventory_grid = inventory_grid
        ctx.inventory_patches = patches
        save_state(state_path, ctx)
        print(f"Grid saved to {state_path}")

    window = OverlayWindow(
        screen_rect=screen_rect,
        screen=screen,
        initial_rect=merchant_rect,
        initial_inventory_rect=inventory_rect,
        on_save=_save,
    )
    window.show()
    sys.exit(app.exec())


def main() -> None:
    parser = argparse.ArgumentParser(description="Merchant grid calibrator overlay")
    parser.add_argument(
        "--state-path",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "state.json",
        help="Путь до state.json",
    )
    args = parser.parse_args()
    run_calibrator(args.state_path)


if __name__ == "__main__":
    main()
