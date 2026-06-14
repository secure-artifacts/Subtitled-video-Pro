from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True)
class BoardItem:
    item_id: str
    rect: Rect


@dataclass(frozen=True)
class DragGhost:
    item_id: str
    x: float
    y: float
    width: float
    height: float
    opacity: float = 0.58


def normalized_rect(x1: float, y1: float, x2: float, y2: float) -> Rect:
    left = min(float(x1), float(x2))
    top = min(float(y1), float(y2))
    right = max(float(x1), float(x2))
    bottom = max(float(y1), float(y2))
    return Rect(left, top, right - left, bottom - top)


def intersects(a: Rect, b: Rect) -> bool:
    return not (a.right < b.x or b.right < a.x or a.bottom < b.y or b.bottom < a.y)


def hit_test_items(items: Iterable[BoardItem], selection_rect: Rect) -> set[str]:
    return {item.item_id for item in items if item.item_id and intersects(item.rect, selection_rect)}


def merge_selection(
    current: Iterable[str],
    hit_ids: Iterable[str],
    *,
    additive: bool = False,
    subtractive: bool = False,
) -> set[str]:
    selected = set(current or [])
    hits = set(hit_ids or [])
    if subtractive:
        return selected - hits
    if additive:
        return selected | hits
    return hits


def contiguous_range_ids(ordered_ids: Iterable[str], anchor_id: str, target_id: str) -> list[str]:
    ordered = [item for item in ordered_ids or [] if item]
    if not ordered:
        return []
    try:
        start = ordered.index(anchor_id)
    except ValueError:
        start = 0
    try:
        end = ordered.index(target_id)
    except ValueError:
        end = start
    if start > end:
        start, end = end, start
    return ordered[start : end + 1]


def drag_ghosts(
    items_by_id: Mapping[str, Rect],
    selected_ids: Iterable[str],
    *,
    delta_x: float,
    delta_y: float,
    opacity: float = 0.58,
) -> list[DragGhost]:
    ghosts: list[DragGhost] = []
    for item_id in selected_ids or []:
        rect = items_by_id.get(item_id)
        if rect is None:
            continue
        ghosts.append(
            DragGhost(
                item_id=item_id,
                x=rect.x + float(delta_x),
                y=rect.y + float(delta_y),
                width=rect.width,
                height=rect.height,
                opacity=opacity,
            )
        )
    return ghosts


def bounding_rect(rects: Iterable[Rect]) -> Rect:
    rects = [rect for rect in rects if rect is not None]
    if not rects:
        return Rect(0.0, 0.0, 0.0, 0.0)
    left = min(rect.x for rect in rects)
    top = min(rect.y for rect in rects)
    right = max(rect.right for rect in rects)
    bottom = max(rect.bottom for rect in rects)
    return Rect(left, top, right - left, bottom - top)
