"""Кастомные графические виджеты с векторной отрисовкой и анимацией."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, BORDER, DANGER, TEXT_PRIMARY, TEXT_SECONDARY, WARNING


def draw_icon(painter: QPainter, key: str, rect: QRectF, color: QColor) -> None:
    """Рисует чёткую векторную иконку раздела в заданном прямоугольнике."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    cx, cy = rect.center().x(), rect.center().y()
    r = min(rect.width(), rect.height()) * 0.38

    if key == "overview":
        # Сетка 2x2 дашборда
        s = r * 0.82
        gap = r * 0.22
        painter.drawRoundedRect(QRectF(cx - s, cy - s, s - gap / 2, s - gap / 2), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(cx + gap / 2, cy - s, s - gap / 2, s - gap / 2), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(cx - s, cy + gap / 2, s - gap / 2, s - gap / 2), 2.5, 2.5)
        painter.drawRoundedRect(QRectF(cx + gap / 2, cy + gap / 2, s - gap / 2, s - gap / 2), 2.5, 2.5)
    elif key == "cpu":
        # Чип процессора с ножками
        box = QRectF(cx - r * 0.65, cy - r * 0.65, r * 1.3, r * 1.3)
        painter.drawRoundedRect(box, 3, 3)
        inner = QRectF(cx - r * 0.3, cy - r * 0.3, r * 0.6, r * 0.6)
        painter.drawRect(inner)
        for offset in (-r * 0.35, 0.0, r * 0.35):
            painter.drawLine(QPointF(cx + offset, cy - r), QPointF(cx + offset, cy - r * 0.65))
            painter.drawLine(QPointF(cx + offset, cy + r * 0.65), QPointF(cx + offset, cy + r))
            painter.drawLine(QPointF(cx - r, cy + offset), QPointF(cx - r * 0.65, cy + offset))
            painter.drawLine(QPointF(cx + r * 0.65, cy + offset), QPointF(cx + r, cy + offset))
    elif key == "memory":
        # Планка ОЗУ
        bar = QRectF(cx - r, cy - r * 0.5, r * 2, r)
        painter.drawRoundedRect(bar, 2.5, 2.5)
        for offset in (-r * 0.55, 0.0, r * 0.55):
            painter.drawRect(QRectF(cx + offset - r * 0.16, cy - r * 0.22, r * 0.32, r * 0.44))
    elif key == "disks":
        # Цилиндр / накопитель
        box = QRectF(cx - r * 0.85, cy - r * 0.6, r * 1.7, r * 1.2)
        painter.drawRoundedRect(box, 3.5, 3.5)
        painter.drawLine(QPointF(cx - r * 0.85, cy + r * 0.1), QPointF(cx + r * 0.85, cy + r * 0.1))
        painter.drawEllipse(QPointF(cx + r * 0.45, cy + r * 0.35), r * 0.1, r * 0.1)
    elif key == "video":
        # Видеокарта с кулером
        box = QRectF(cx - r * 0.95, cy - r * 0.6, r * 1.9, r * 1.2)
        painter.drawRoundedRect(box, 3, 3)
        painter.drawEllipse(QPointF(cx + r * 0.15, cy), r * 0.38, r * 0.38)
        painter.drawLine(QPointF(cx - r * 0.55, cy - r * 0.3), QPointF(cx - r * 0.55, cy + r * 0.3))
    elif key == "monitors":
        # Экран монитора на подставке
        scr = QRectF(cx - r * 0.95, cy - r * 0.75, r * 1.9, r * 1.2)
        painter.drawRoundedRect(scr, 3, 3)
        painter.drawLine(QPointF(cx, cy + r * 0.45), QPointF(cx, cy + r * 0.85))
        painter.drawLine(QPointF(cx - r * 0.45, cy + r * 0.85), QPointF(cx + r * 0.45, cy + r * 0.85))
    elif key == "network":
        # Узлы сети
        painter.drawEllipse(QPointF(cx, cy - r * 0.55), r * 0.25, r * 0.25)
        painter.drawEllipse(QPointF(cx - r * 0.65, cy + r * 0.5), r * 0.25, r * 0.25)
        painter.drawEllipse(QPointF(cx + r * 0.65, cy + r * 0.5), r * 0.25, r * 0.25)
        painter.drawLine(QPointF(cx - r * 0.15, cy - r * 0.35), QPointF(cx - r * 0.5, cy + r * 0.3))
        painter.drawLine(QPointF(cx + r * 0.15, cy - r * 0.35), QPointF(cx + r * 0.5, cy + r * 0.3))
        painter.drawLine(QPointF(cx - r * 0.4, cy + r * 0.5), QPointF(cx + r * 0.4, cy + r * 0.5))
    elif key == "audio":
        # Динамик и звуковая волна
        path = QPainterPath()
        path.moveTo(cx - r * 0.75, cy - r * 0.3)
        path.lineTo(cx - r * 0.35, cy - r * 0.3)
        path.lineTo(cx + r * 0.1, cy - r * 0.7)
        path.lineTo(cx + r * 0.1, cy + r * 0.7)
        path.lineTo(cx - r * 0.35, cy + r * 0.3)
        path.lineTo(cx - r * 0.75, cy + r * 0.3)
        path.closeSubpath()
        painter.drawPath(path)
        arc_rect = QRectF(cx - r * 0.1, cy - r * 0.55, r * 0.85, r * 1.1)
        painter.drawArc(arc_rect, -55 * 16, 110 * 16)
    elif key == "drivers":
        # Шестерёнка / гаечный модуль
        painter.drawEllipse(QPointF(cx, cy), r * 0.42, r * 0.42)
        for angle_deg in range(0, 360, 45):
            painter.save()
            painter.translate(cx, cy)
            painter.rotate(angle_deg)
            painter.drawLine(QPointF(0, -r * 0.52), QPointF(0, -r * 0.85))
            painter.restore()
    elif key == "cleaner":
        # Корзина очистки / щётка
        painter.drawLine(QPointF(cx - r * 0.75, cy - r * 0.45), QPointF(cx + r * 0.75, cy - r * 0.45))
        painter.drawLine(QPointF(cx - r * 0.3, cy - r * 0.75), QPointF(cx + r * 0.3, cy - r * 0.75))
        body = QRectF(cx - r * 0.58, cy - r * 0.45, r * 1.16, r * 1.25)
        painter.drawRoundedRect(body, 2.5, 2.5)
        painter.drawLine(QPointF(cx - r * 0.2, cy - r * 0.15), QPointF(cx - r * 0.2, cy + r * 0.5))
        painter.drawLine(QPointF(cx + r * 0.2, cy - r * 0.15), QPointF(cx + r * 0.2, cy + r * 0.5))
    elif key == "optimize":
        # Молния ускорения / оптимизации
        bolt = QPainterPath()
        bolt.moveTo(cx + r * 0.15, cy - r * 0.9)
        bolt.lineTo(cx - r * 0.65, cy + r * 0.1)
        bolt.lineTo(cx - r * 0.05, cy + r * 0.1)
        bolt.lineTo(cx - r * 0.2, cy + r * 0.9)
        bolt.lineTo(cx + r * 0.65, cy - r * 0.1)
        bolt.lineTo(cx + r * 0.05, cy - r * 0.1)
        bolt.closeSubpath()
        painter.drawPath(bolt)
    else:
        # Облако / стрелка обновления
        arc = QRectF(cx - r * 0.8, cy - r * 0.8, r * 1.6, r * 1.6)
        painter.drawArc(arc, 30 * 16, 300 * 16)
        painter.drawLine(QPointF(cx, cy - r * 0.45), QPointF(cx, cy + r * 0.35))
        painter.drawLine(QPointF(cx - r * 0.35, cy + r * 0.05), QPointF(cx, cy + r * 0.4))
        painter.drawLine(QPointF(cx + r * 0.35, cy + r * 0.05), QPointF(cx, cy + r * 0.4))

    painter.restore()


class SpinnerWidget(QWidget):
    """Анимированный векторный индикатор выполнения."""

    def __init__(self, size: int = 22) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._angle = 0
        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0)
        self._anim.setEndValue(360)
        self._anim.setDuration(950)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_tick)
        self.hide()

    def _on_tick(self, value: object) -> None:
        self._angle = int(value)
        self.update()

    def start(self) -> None:
        self.show()
        if self._anim.state() != QVariantAnimation.State.Running:
            self._anim.start()

    def stop(self) -> None:
        self._anim.stop()
        self.hide()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
        track_pen = QPen(QColor(BORDER), 2.6)
        painter.setPen(track_pen)
        painter.drawEllipse(rect)

        arc_pen = QPen(QColor(ACCENT), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(arc_pen)
        painter.drawArc(rect, -self._angle * 16, 100 * 16)


class AnimatedRingGauge(QWidget):
    """Кольцевой график (Donut Gauge) с плавной анимацией заполнения."""

    def __init__(self, title: str, subtitle: str = "", accent_color: str = ACCENT) -> None:
        super().__init__()
        self.setMinimumSize(155, 165)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._title = title
        self._subtitle = subtitle
        self._accent = QColor(accent_color)
        self._current = 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setDuration(750)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def set_data(self, percent: float, title: str | None = None, subtitle: str | None = None) -> None:
        if title is not None:
            self._title = title
        if subtitle is not None:
            self._subtitle = subtitle
        clamped = max(0.0, min(100.0, percent))
        if clamped >= 88:
            self._accent = QColor(DANGER)
        elif clamped >= 72:
            self._accent = QColor(WARNING)
        else:
            self._accent = QColor(ACCENT)
        self._anim.stop()
        self._anim.setStartValue(self._current)
        self._anim.setEndValue(clamped)
        self._anim.start()

    def _on_anim(self, val: object) -> None:
        self._current = float(val)
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w, h = self.width(), self.height()
        diameter = min(w - 24, h - 52)
        diameter = max(diameter, 76)
        x = (w - diameter) / 2
        y = 8.0
        rect = QRectF(x, y, diameter, diameter)

        # Фоновое кольцо
        pen_bg = QPen(QColor("#222c3d"), 8.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
        painter.setPen(pen_bg)
        painter.drawArc(rect, 0, 360 * 16)

        # Заполненная дуга с градиентом
        if self._current > 0.3:
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0.0, self._accent)
            grad.setColorAt(1.0, self._accent.lighter(130))
            pen_fg = QPen(grad, 8.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            painter.setPen(pen_fg)
            span = int(-self._current / 100.0 * 360 * 16)
            painter.drawArc(rect, 90 * 16, span)

        # Процент внутри кольца
        painter.setPen(QColor(TEXT_PRIMARY))
        font_val = QFont("Segoe UI", 13, QFont.Weight.Bold)
        painter.setFont(font_val)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, f"{self._current:.0f}%")

        # Подписи снизу
        title_rect = QRectF(4, y + diameter + 6, w - 8, 20)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.setPen(QColor(TEXT_PRIMARY))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, self._title)

        sub_rect = QRectF(4, y + diameter + 24, w - 8, 18)
        painter.setFont(QFont("Segoe UI", 9))
        painter.setPen(QColor(TEXT_SECONDARY))
        painter.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, self._subtitle)


class HeroCard(QFrame):
    """Карточка ключевого показателя системы на дашборде."""

    def __init__(self, icon_key: str, category: str, accent: str = ACCENT) -> None:
        super().__init__()
        self.setObjectName("CardHover")
        self.setMinimumHeight(106)
        self._icon_key = icon_key
        self._accent = QColor(accent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(4)

        top = QHBoxLayout()
        top.setContentsMargins(32, 0, 0, 0)
        self._cat_label = QLabel(category.upper())
        self._cat_label.setStyleSheet(f"color: {TEXT_SECONDARY}; font-size: 11px; font-weight: 700; letter-spacing: 0.6px;")
        top.addWidget(self._cat_label)
        top.addStretch(1)
        layout.addLayout(top)

        self._val_label = QLabel("—")
        self._val_label.setStyleSheet(f"color: {TEXT_PRIMARY}; font-size: 15px; font-weight: 700;")
        self._val_label.setWordWrap(True)
        layout.addWidget(self._val_label)

        self._sub_label = QLabel("")
        self._sub_label.setObjectName("MutedLabel")
        layout.addWidget(self._sub_label)
        layout.addStretch(1)

    def set_content(self, value: str, subtitle: str = "") -> None:
        self._val_label.setText(value or "—")
        self._sub_label.setText(subtitle)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        # Левая акцентная линия
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._accent)
        painter.drawRoundedRect(QRectF(0, 14, 3.5, self.height() - 28), 2, 2)
        # Иконка
        icon_rect = QRectF(14, 11, 22, 22)
        draw_icon(painter, self._icon_key, icon_rect, self._accent)


class SidebarButton(QPushButton):
    """Кнопка навигации в боковой панели с векторной иконкой и бейджем."""

    def __init__(self, key: str, title: str) -> None:
        super().__init__(title)
        self.key = key
        self._badge = ""
        self._active = False
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(40)

    def set_active(self, active: bool) -> None:
        self._active = active
        self.setChecked(active)
        self.update()

    def set_badge(self, text: str) -> None:
        self._badge = text
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(4, 2, self.width() - 8, self.height() - 4)

        if self._active:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(59, 130, 246, 38))
            painter.drawRoundedRect(rect, 8, 8)
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(QRectF(4, 10, 3.5, self.height() - 20), 2, 2)
        elif self.underMouse():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, 12))
            painter.drawRoundedRect(rect, 8, 8)

        icon_color = QColor(ACCENT) if self._active else QColor(TEXT_SECONDARY)
        draw_icon(painter, self.key, QRectF(14, (self.height() - 20) / 2, 20, 20), icon_color)

        text_color = QColor(TEXT_PRIMARY) if self._active else QColor(TEXT_SECONDARY)
        painter.setPen(text_color)
        font = QFont("Segoe UI", 10, QFont.Weight.DemiBold if self._active else QFont.Weight.Normal)
        painter.setFont(font)
        text_rect = QRectF(42, 0, self.width() - 88, self.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())

        if self._badge:
            badge_w = max(24, len(self._badge) * 7 + 12)
            badge_rect = QRectF(self.width() - badge_w - 12, (self.height() - 20) / 2, badge_w, 20)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ACCENT if self._active else "#253045"))
            painter.drawRoundedRect(badge_rect, 10, 10)
            painter.setPen(QColor("#ffffff" if self._active else TEXT_SECONDARY))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, self._badge)


class AnimatedStackedWidget(QStackedWidget):
    """Стек страниц с плавной анимацией прозрачности при переключении."""

    def __init__(self) -> None:
        super().__init__()
        self._anim: QPropertyAnimation | None = None
        self._anim_widget: QWidget | None = None

    def _cleanup_effect(self) -> None:
        if self._anim is not None:
            self._anim.stop()
            self._anim = None
        if self._anim_widget is not None:
            self._anim_widget.setGraphicsEffect(None)
            self._anim_widget = None

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        if index == self.currentIndex():
            return
        self._cleanup_effect()
        super().setCurrentIndex(index)
        widget = self.currentWidget()
        if widget is None:
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        self._anim_widget = widget
        anim = QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(180)
        anim.setStartValue(0.15)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(self._cleanup_effect)
        self._anim = anim
        anim.start()

