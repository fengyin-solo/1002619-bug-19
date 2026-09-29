"""货物装卸业务规则：填报单暂存、舱位查询、装机去重落库与并发先后判定都收在这里。

口径约定：
- 填报单可以暂存，缺字段不拦，但会逐项列出缺哪一项；
- 装机提交前先按货邮编号去重，同一批数据重复送达只留最新一版；
- 不同批次同时装机时以先落库的那份为准（乐观并发，版本号判定）；
- 任何一次装机失败都不动已落库的板箱数量，已录的板箱数不能少。
"""
from __future__ import annotations

from typing import Any

from app.store import store

MODULE = "cargo"
DRAFT_FIELDS = ["货邮编号", "对应航班", "货物类型", "总重吨位", "板箱数量", "装卸班组", "舱位分配"]
# 装机落库前必填的字段
LOAD_REQUIRED = ["货邮编号", "板箱数量", "舱位分配"]
STATUS_ORDER = ["待装卸", "装卸中", "已装机", "已入库"]
ACTION_RULES = {"安排装卸": "装卸中", "开始装机": "已装机", "确认入库": "已入库"}
NEGATIVE_ACTIONS = []
# 模拟舱位分配上游不稳定：航班号带 FAIL 时返回查不到，前端可重试
CABIN_FAIL_MARK = "FAIL"


def _is_blank(value: Any) -> bool:
    return value is None or str(value).strip() == ""


def _missing(values: dict[str, Any], fields: list[str]) -> list[str]:
    return [field for field in fields if _is_blank(values.get(field))]


def _clean(values: dict[str, Any]) -> dict[str, Any]:
    """只保留填报单里出现过且非空的字段，避免空值覆盖已录内容。"""
    return {field: values[field] for field in DRAFT_FIELDS if field in values and not _is_blank(values[field])}


class CargoService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("货邮编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = _missing(values, ["货邮编号", "对应航班", "货物类型"])
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in ["货邮编号", "对应航班", "货物类型"]})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        entry["version"] = 1
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"货邮任务 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于货物装卸可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"货邮任务已{action}"

    # ---- 填报单暂存 ----
    def get_draft(self) -> dict[str, Any]:
        return store.draft(MODULE)

    def save_draft(self, values: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        draft = store.save_draft(MODULE, _clean(values))
        return draft, _missing(draft, LOAD_REQUIRED)

    # ---- 舱位分配查询 ----
    def query_cabin(self, flight: str | None) -> tuple[dict[str, Any] | None, str | None]:
        if _is_blank(flight):
            return None, "缺少对应航班，无法查询舱位分配"
        if CABIN_FAIL_MARK in str(flight).upper():
            return None, "舱位分配服务暂时查不到该航班，请稍后重试"
        return {"航班": flight, "舱位分配": f"{flight}-主货舱", "可装板位": 12}, None

    # ---- 装机提交 ----
    def submit_loading(
        self,
        values: dict[str, Any],
        *,
        batch_id: str | None = None,
        base_version: int | None = None,
    ) -> tuple[dict[str, Any] | None, str, list[str]]:
        cleaned = _clean(values)
        missing = _missing(cleaned, LOAD_REQUIRED)
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}", missing
        try:
            count = int(cleaned["板箱数量"])
        except (TypeError, ValueError):
            return None, "板箱数量必须是整数", ["板箱数量"]
        if count <= 0:
            return None, "板箱数量必须大于 0", ["板箱数量"]
        cleaned["板箱数量"] = count

        number = str(cleaned["货邮编号"]).strip()
        rows = store.rows(MODULE)
        existing = next((row for row in rows if str(row.get("货邮编号", "")).strip() == number), None)

        if existing is None:
            if base_version not in (None, "", 0):
                return None, "该货邮编号尚未落库却带了版本号，数据已过期，请刷新后重试", []
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: cleaned[field] for field in DRAFT_FIELDS if field in cleaned})
            entry["货邮编号"] = number
            entry["status"] = "已装机"
            entry["pending"] = False
            entry["abnormal"] = False
            entry["version"] = 1
            entry["batch_id"] = batch_id
            rows.append(entry)
            store.clear_draft(MODULE)
            return entry, "装机数据已落库", []

        # 已存在：先看是不是同一批数据重复送达，重复只留最新一版
        if batch_id and existing.get("batch_id") == batch_id:
            self._apply_loading(existing, cleaned)
            existing["version"] = int(existing.get("version", 1)) + 1
            store.clear_draft(MODULE)
            return existing, f"货邮编号 {number} 装机数据已更新到最新一版", []

        # 不同批次：乐观并发，先落库为准
        current_version = int(existing.get("version", 1))
        if base_version is None or str(base_version) == "" or int(base_version) != current_version:
            return None, f"货邮编号 {number} 已被他人先落库（最新版本 {current_version}），请刷新后再改", []
        self._apply_loading(existing, cleaned)
        existing["version"] = current_version + 1
        existing["batch_id"] = batch_id
        store.clear_draft(MODULE)
        return existing, f"货邮编号 {number} 装机数据已更新", []

    @staticmethod
    def _apply_loading(entry: dict[str, Any], cleaned: dict[str, Any]) -> None:
        for field in DRAFT_FIELDS:
            if field in cleaned:
                entry[field] = cleaned[field]
        entry["status"] = "已装机"
        entry["pending"] = False

    # ---- 三处口径一致：都从同一份台账里读 ----
    def pending_entries(self) -> tuple[list[dict[str, Any]], int]:
        rows = [row for row in store.rows(MODULE) if row.get("status") in ("待装卸", "装卸中")]
        return rows, len(rows)

    def loading_details(self) -> tuple[list[dict[str, Any]], int]:
        rows = [row for row in store.rows(MODULE) if row.get("status") in ("已装机", "已入库")]
        return rows, len(rows)
